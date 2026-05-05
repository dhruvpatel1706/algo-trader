"""LLM-discretionary lane: sandboxed Claude paper agent.

HARD constraints:
- Always paper-only; never reaches live broker codepath.
- Subject to standard risk gates (src/risk/limits.py).
- Decisions logged to journal as discretionary entries (downstream wiring).
- Cannot raise risk caps; cannot bypass coherence halt.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

# Module-level invariant mirror — see `tests/unit/moonshot/test_bridge_invariant.py`.
LIVE_BROKER_BRIDGE: bool = False


@dataclass(frozen=True, slots=True)
class LlmDecision:
    ticker: str
    action: Literal["buy", "sell", "hold", "abstain"]
    confidence: float
    reasoning: str
    model: str
    ts: datetime


def _abstain(model: str, ticker: str, reason: str) -> LlmDecision:
    return LlmDecision(
        ticker=ticker,
        action="abstain",
        confidence=0.0,
        reasoning=reason,
        model=model,
        ts=datetime.now(UTC),
    )


class LlmDiscretionaryAgent:
    """Sandboxed Claude paper agent.

    Constraints (HARD):
    - Always paper-only; never reaches live broker codepath
    - Subject to standard risk gates (src/risk/limits.py)
    - Decisions logged to journal as discretionary entries
    - Cannot raise risk caps; cannot bypass coherence halt
    """

    # Lane safety flag. Tests assert this stays False.
    LIVE_BROKER_BRIDGE: bool = False

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        client: Any | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._client = client
        # api_key resolution allows tests to inject; otherwise read env at decide-time.
        self._api_key = api_key

    def _build_prompt(self, market_state: dict, today: date) -> list[dict]:
        """Anonymized tickers + date constraint. Returns Anthropic-style messages."""
        # Anonymise tickers to {T1, T2, ...} so the LLM cannot leverage memorized
        # ticker-specific narratives.
        tickers = list(market_state.get("tickers", {}).keys())
        alias = {t: f"T{i + 1}" for i, t in enumerate(tickers)}
        anon_state = {
            alias[t]: market_state["tickers"][t] for t in tickers
        }
        system = (
            "You are a sandboxed paper-trading research agent. You will receive "
            "anonymized market state and must return a JSON object with keys "
            "{ticker, action, confidence, reasoning}. action must be one of "
            "buy|sell|hold|abstain. Confidence in [0,1]. Decisions are PAPER-ONLY."
        )
        user_payload = {
            "as_of": today.isoformat(),
            "anonymized_state": anon_state,
            "instructions": (
                "Pick one ticker alias (e.g. T1) and an action. Return ONLY a JSON "
                "object. Do not include any commentary outside JSON."
            ),
        }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload)},
        ]

    def _resolve_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        api_key = self._api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        try:
            import anthropic  # type: ignore

            return anthropic.Anthropic(api_key=api_key)
        except Exception:
            return None

    def _parse_response(self, text: str, fallback_ticker: str) -> LlmDecision:
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return _abstain(self._model, fallback_ticker, "unparseable response")

        action = str(data.get("action", "abstain")).lower()
        if action not in {"buy", "sell", "hold", "abstain"}:
            action = "abstain"
        ticker = str(data.get("ticker", fallback_ticker))
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        reasoning = str(data.get("reasoning", ""))[:2000]

        return LlmDecision(
            ticker=ticker,
            action=action,  # type: ignore[arg-type]
            confidence=confidence,
            reasoning=reasoning,
            model=self._model,
            ts=datetime.now(UTC),
        )

    def _extract_text(self, response: Any) -> str:
        """Pull text out of an Anthropic-style messages.create response.

        Tolerates dict-like and object-like responses (mocks frequently use dicts).
        """
        # Object form: response.content[0].text
        try:
            content = response.content  # type: ignore[attr-defined]
        except AttributeError:
            content = None
        if content is None and isinstance(response, dict):
            content = response.get("content")
        if not content:
            return ""
        first = content[0]
        text = getattr(first, "text", None)
        if text is None and isinstance(first, dict):
            text = first.get("text", "")
        return text or ""

    def decide(self, market_state: dict, today: date) -> LlmDecision:
        """Build a prompt with anonymized tickers + date constraint, call Anthropic API,
        parse decision, return LlmDecision. Returns 'abstain' if API unavailable.

        DOES NOT execute the decision. Returns it for the standard risk pipeline
        to evaluate.
        """
        if self.LIVE_BROKER_BRIDGE:  # pragma: no cover - safety guard
            raise RuntimeError("LlmDiscretionaryAgent MUST never bridge to a live broker.")

        tickers = list(market_state.get("tickers", {}).keys())
        fallback_ticker = tickers[0] if tickers else "UNKNOWN"

        client = self._resolve_client()
        if client is None:
            return _abstain(self._model, fallback_ticker, "no API client available")

        messages = self._build_prompt(market_state, today)
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=512,
                system=messages[0]["content"],
                messages=[messages[1]],
            )
        except Exception as e:
            return _abstain(self._model, fallback_ticker, f"API error: {type(e).__name__}")

        text = self._extract_text(response)
        if not text:
            return _abstain(self._model, fallback_ticker, "empty response body")

        return self._parse_response(text, fallback_ticker)
