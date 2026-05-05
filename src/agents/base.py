"""Agent base class + status DTO + shared enums.

Agents are per-asset-class containers. Each Agent runs N strategies on its
universe and contributes to a shared portfolio. They are NOT separate processes
— they are configured strategy bundles with metadata (asset class, allocation,
coherence tracking) that the engine consumes.

Coherence is the live-vs-backtest agreement signal we use to flag strategies
whose realized behavior diverges from the assumptions baked into the backtest.
We carry it on the agent so the governance layer can read it without pinning a
specific live-vs-backtest implementation here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.strategies.base import Signal, Strategy


class AssetClass(StrEnum):
    """Coarse asset-class buckets that map 1:1 to agent types in v1."""

    EQUITY = "equity"
    GOLD = "gold"
    BONDS = "bonds"
    CRYPTO = "crypto"
    GOVERNANCE = "governance"


@dataclass(slots=True)
class AgentStatus:
    """Snapshot of an agent's runtime state.

    Plain data — safe to publish to Redis or render on the dashboard.
    `coherence` is NaN until enough live data exists to compute the ratio.
    """

    name: str
    asset_class: AssetClass
    state: str  # "live" | "paper" | "halted"
    heat_allocation: float  # 0..1
    coherence: float  # live_WR / backtest_WR ratio (NaN if no live data yet)
    n_open_positions: int
    last_eval_ts: datetime | None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict (Enum -> str, datetime -> isoformat)."""
        d = asdict(self)
        d["asset_class"] = self.asset_class.value
        if self.last_eval_ts is not None:
            d["last_eval_ts"] = self.last_eval_ts.isoformat()
        return d


class Agent(ABC):
    """Base class for asset-class trading agents.

    Each agent wraps a list of strategies, owns a slice of total portfolio heat,
    and tracks coherence for kill/promote decisions.

    v1 is pure data — no Redis, no broker, no live state. The engine binds an
    agent to a real execution loop later.
    """

    name: str = "<unnamed>"
    asset_class: AssetClass = AssetClass.EQUITY

    def __init__(
        self,
        strategies: Iterable[Strategy],
        universe: Iterable[str],
        heat_allocation: float = 0.0,
    ) -> None:
        self.strategies: list[Strategy] = list(strategies)
        self.universe: tuple[str, ...] = tuple(universe)
        if not (0.0 <= heat_allocation <= 1.0):
            raise ValueError("heat_allocation must be in [0, 1]")
        self.heat_allocation = heat_allocation
        self._state: str = "paper"
        self._coherence: float = float("nan")
        self._last_eval_ts: datetime | None = None
        self._n_open_positions: int = 0

    @abstractmethod
    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        """Run all strategies on the bars, return list[Signal]."""

    def status(self) -> AgentStatus:
        """Return a snapshot of the agent's current state."""
        return AgentStatus(
            name=self.name,
            asset_class=self.asset_class,
            state=self._state,
            heat_allocation=self.heat_allocation,
            coherence=self._coherence,
            n_open_positions=self._n_open_positions,
            last_eval_ts=self._last_eval_ts,
        )
