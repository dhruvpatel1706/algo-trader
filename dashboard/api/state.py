"""In-memory dashboard state — strategy pause/resume + LLM cost counter.

Persistent state (positions, orders, trades) is read live from Alpaca + journals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock


@dataclass
class _StrategyStatus:
    name: str
    enabled: bool = True
    paused_at: datetime | None = None


@dataclass
class _CostCounter:
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    api_requests: int = 0
    estimated_usd: float = 0.0


class DashboardState:
    """Tiny thread-safe in-memory store. Populated by API routes + bus consumer."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._strategies: dict[str, _StrategyStatus] = {
            "mr_etf": _StrategyStatus("mr_etf"),
            "wheel_etf": _StrategyStatus("wheel_etf"),
        }
        self._costs = _CostCounter()
        self._halted = False
        self._halted_reason: str | None = None
        self._halted_at: datetime | None = None
        self._agent_events: list[dict] = []  # ring buffer of recent agent events

    # --- strategies ---

    def list_strategies(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "name": s.name,
                    "enabled": s.enabled and not self._halted,
                    "paused_at": s.paused_at.isoformat() if s.paused_at else None,
                }
                for s in self._strategies.values()
            ]

    def pause(self, name: str) -> bool:
        with self._lock:
            s = self._strategies.get(name)
            if not s:
                return False
            s.enabled = False
            s.paused_at = datetime.now(UTC)
            return True

    def resume(self, name: str) -> bool:
        with self._lock:
            s = self._strategies.get(name)
            if not s:
                return False
            s.enabled = True
            s.paused_at = None
            return True

    # --- halt ---

    def halt(self, reason: str) -> None:
        with self._lock:
            self._halted = True
            self._halted_reason = reason
            self._halted_at = datetime.now(UTC)
            for s in self._strategies.values():
                s.enabled = False
                s.paused_at = self._halted_at

    def reset_halt(self) -> None:
        with self._lock:
            self._halted = False
            self._halted_reason = None
            self._halted_at = None

    def halt_status(self) -> dict:
        with self._lock:
            return {
                "halted": self._halted,
                "reason": self._halted_reason,
                "at": self._halted_at.isoformat() if self._halted_at else None,
            }

    # --- costs ---

    def add_cost(
        self, *, input_tokens: int = 0, output_tokens: int = 0, requests: int = 0, usd: float = 0.0
    ) -> None:
        with self._lock:
            self._costs.llm_input_tokens += input_tokens
            self._costs.llm_output_tokens += output_tokens
            self._costs.api_requests += requests
            self._costs.estimated_usd += usd

    def costs(self) -> dict:
        with self._lock:
            return {
                "llm_input_tokens": self._costs.llm_input_tokens,
                "llm_output_tokens": self._costs.llm_output_tokens,
                "api_requests": self._costs.api_requests,
                "estimated_usd": round(self._costs.estimated_usd, 4),
            }

    # --- agent activity ring buffer ---

    def push_agent_event(self, event: dict, *, max_buffer: int = 200) -> None:
        with self._lock:
            self._agent_events.append(event)
            if len(self._agent_events) > max_buffer:
                del self._agent_events[: len(self._agent_events) - max_buffer]

    def recent_agent_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._agent_events[-limit:])


# Module-level singleton — FastAPI deps return this.
_state: DashboardState | None = None


def get_state() -> DashboardState:
    global _state
    if _state is None:
        _state = DashboardState()
    return _state
