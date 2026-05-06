"""Autonomous LLM reasoner that evaluates a candidate signal before placement.

This is the headline feature: instead of a chatbot the operator queries,
the **bot itself** uses an LLM to *understand* every candidate trade
before the existing risk gate sees it. The LLM never decides alone — it
returns a structured judgment that the rule-based pipeline composes with
its own confidence + the risk caps.

Pipeline integration::

    rule-based signal generated  (e.g. failed_breakout fires on SPY)
              │
              ▼
    AutonomousReasoner.evaluate(signal, context)
              │     ↳ LLM router (Anthropic Haiku → Gemini Flash → OpenAI)
              ▼
    SignalJudgment{multiplier, halt, reasoning}
              │
              ▼
    signal.confidence *= judgment.multiplier
              │
              ▼
    risk gate (`src/risk/limits.check_limits`) — same as before
              │
              ▼
    compliance gate
              │
              ▼
    `PaperBroker.submit` (or `--dry-run`)

Hard rules:
  1. The reasoner returns a multiplier in [0.5, 1.2]. It cannot UPSIZE a
     signal beyond +20% — the rule-based confidence stays the anchor.
  2. The reasoner CANNOT bypass the risk gate. It can only **dampen** a
     signal (multiplier < 1) or veto via `halt=True`. It cannot lift a
     position size above what the risk gate would have allowed.
  3. Every evaluation is journaled with the prompt, response, multiplier,
     and reasoning. Auditable end-to-end.
  4. When LLM is unavailable, the reasoner returns multiplier=1.0 and
     halt=False (fail open — let the rule-based system run unmodified).
     This is deliberate: a chronic LLM outage must NOT take the bot down.
  5. Tickers are anonymized to ``[ASSET_<id>]`` before being sent to the
     LLM, per the Deep90 anti-bias pattern. This prevents the LLM from
     using its training-data prior for a specific ticker.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from src.llm import LLMUnavailableError, call_llm
from src.runtime.market_phase import (
    AssetClass,
    MarketPhase,
    current_phase,
    phase_posture,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hard-coded bounds on what the reasoner can do.
# ---------------------------------------------------------------------------

# A reasoner output outside these bounds is clipped before the strategy
# pipeline ever sees it. Locking these values constants here means a future
# prompt-injection or confused-LLM response can't issue a 10x sizing.
MULTIPLIER_FLOOR: float = 0.5
MULTIPLIER_CEILING: float = 1.2

# Maximum context window the reasoner is allowed to send to the LLM. Larger
# context = larger cost = slower per-eval cycle. Tuned for sub-second latency.
MAX_CONTEXT_BARS: int = 20
MAX_NEWS_HEADLINES: int = 5


@dataclass(frozen=True, slots=True)
class SignalContext:
    """Everything the reasoner sees about a candidate signal.

    Anonymization happens at construction time of the LLM prompt — not here.
    Callers pass real tickers/numbers; the reasoner anonymizes only the
    LLM-bound text.
    """

    symbol: str
    side: Literal["buy", "sell"]
    strategy: str
    rule_confidence: float                      # the strategy's own confidence
    entry_price: float
    stop_price: float
    target_price: float | None
    recent_bars: list[dict[str, Any]] = field(default_factory=list)  # OHLCV dicts
    regime: str | None = None                   # e.g. "risk_off", "neutral"
    insider_score: float | None = None          # 0..1 if we have Form 4 data
    news_headlines: list[str] = field(default_factory=list)
    open_positions: list[str] = field(default_factory=list)  # ticker symbols
    # Microstructure phase ("pre_market" / "open" / "midday" / "close" / etc.).
    # When None, the reasoner auto-resolves via current_phase() at eval time.
    # Populating this lets the LLM adopt phase-appropriate posture (more
    # cautious in the first 30min after open, more permissive midday).
    phase: MarketPhase | None = None
    asset_class: AssetClass = "equity"          # used only for phase auto-resolve


@dataclass(frozen=True, slots=True)
class SignalJudgment:
    """Structured LLM verdict on the signal.

    `multiplier` is clamped to [MULTIPLIER_FLOOR, MULTIPLIER_CEILING] by
    the reasoner before returning. `halt=True` is the LLM's veto vote — the
    rule-based pipeline can choose to honor it (most strategies should).
    """

    multiplier: float
    halt: bool
    reasoning: str
    provider: str | None
    elapsed_ms: int
    asof: str
    fail_open: bool = False  # True when LLM unavailable -> defaulted to identity


_SYSTEM_PROMPT = (
    "You are a quant risk reviewer for a paper-trading bot. The rule-based "
    "system has already chosen a setup; your job is to spot reasons it "
    "should be DAMPENED or VETOED — not to override it. You see anonymized "
    "tickers ([ASSET_X]) so your training-data prior on the specific name "
    "cannot bias you.\n\n"
    "OUTPUT: STRICT JSON, no prose, no markdown. Shape:\n"
    '  {"multiplier": <float in [0.5, 1.2]>, "halt": <bool>, '
    '"reasoning": "<≤2 sentences>"}\n\n'
    "RULES:\n"
    "1. multiplier > 1.0 only when context strongly supports the setup.\n"
    "2. multiplier < 1.0 when context is mixed or contradictory.\n"
    "3. halt=true only for clear contradictions (e.g. setup is long but "
    "regime is risk_off + insider sells + bearish news cluster).\n"
    "4. You cannot recommend size, you cannot place orders, you cannot "
    "request the operator do anything. Only the multiplier + halt vote.\n"
    "5. If the context is empty or insufficient, multiplier=1.0, halt=false, "
    'reasoning="insufficient context".'
)


@dataclass(slots=True)
class AutonomousReasoner:
    """Construct once; reuse for every signal evaluation.

    Default behavior is fully autonomous: the bot calls `evaluate()` on
    every candidate signal, the LLM judges, and the strategy multiplies
    its own confidence by the result. No human in the loop on a per-trade
    basis — the human-in-the-loop control is the Start/Stop button +
    promotion gates + risk caps, not per-signal approval.
    """

    model_max_tokens: int = 200
    enabled: bool = True
    journal_writer: Any = None  # `JournalWriter | None`; injected at construction

    def evaluate(self, ctx: SignalContext) -> SignalJudgment:
        """Run the LLM judgment. Returns a clamped, journaled SignalJudgment.

        Never raises. LLM-unavailable returns the identity judgment
        (multiplier=1.0, halt=False, fail_open=True) so the rule-based
        pipeline runs unmodified during outages. Any unexpected exception
        in the parsing / journaling layer also degrades to the identity
        judgment — the bot must never be stopped by a reasoner bug.
        """
        started = datetime.now(UTC)
        if not self.enabled:
            return self._identity(started, "reasoner disabled")

        try:
            anon_ctx, aliases = _anonymize_context(ctx)
            user_prompt = _build_user_prompt(anon_ctx)

            try:
                resp = call_llm(
                    system=_SYSTEM_PROMPT,
                    user=user_prompt,
                    max_tokens=self.model_max_tokens,
                    temperature=0.0,
                )
            except LLMUnavailableError as e:
                logger.warning("autonomous_reasoner: LLM unavailable, fail-open: %s", e)
                judgment = self._identity(started, f"LLM unavailable ({e!s})")
                self._journal(ctx, judgment, raw_response=None)
                return judgment

            multiplier, halt, reasoning = _parse_verdict(resp.text)
            # De-anonymize the reasoning so the journal shows real tickers.
            for placeholder, original in aliases.items():
                reasoning = reasoning.replace(placeholder, original)

            elapsed = int((datetime.now(UTC) - started).total_seconds() * 1000)
            judgment = SignalJudgment(
                multiplier=_clamp(multiplier),
                halt=bool(halt),
                reasoning=reasoning,
                provider=resp.provider,
                elapsed_ms=elapsed,
                asof=started.isoformat(),
            )
            self._journal(ctx, judgment, raw_response=resp.text)
            return judgment
        except Exception as e:
            # Last-resort guard: the docstring promises "Never raises". A bug
            # in parsing / aliasing / journaling here must not crash the
            # strategy loop. Log the full traceback once and return identity.
            logger.exception(
                "autonomous_reasoner: unexpected error in evaluate, fail-open"
            )
            judgment = self._identity(started, f"reasoner error ({type(e).__name__})")
            self._journal(ctx, judgment, raw_response=None)
            return judgment

    # -- helpers -----------------------------------------------------------

    def _identity(self, started: datetime, reason: str) -> SignalJudgment:
        return SignalJudgment(
            multiplier=1.0,
            halt=False,
            reasoning=reason,
            provider=None,
            elapsed_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
            asof=started.isoformat(),
            fail_open=True,
        )

    def _journal(
        self,
        ctx: SignalContext,
        judgment: SignalJudgment,
        *,
        raw_response: str | None,
    ) -> None:
        """Best-effort journal write. A journal failure must not break the
        evaluation — we log and move on."""
        if self.journal_writer is None:
            return
        try:
            self.journal_writer.write(
                {
                    "event": "autonomous_reasoner_eval",
                    "symbol": ctx.symbol,
                    "side": ctx.side,
                    "strategy": ctx.strategy,
                    "rule_confidence": ctx.rule_confidence,
                    "judgment": asdict(judgment),
                    "raw_response": raw_response,
                }
            )
        except Exception as e:
            logger.warning("autonomous_reasoner: journal write failed: %s", e)


# ---------------------------------------------------------------------------
# Anonymization + parsing.
# ---------------------------------------------------------------------------


def _anonymize_context(ctx: SignalContext) -> tuple[SignalContext, dict[str, str]]:
    """Replace the candidate ticker + open-position tickers with placeholders.

    The mapping is keyed by placeholder so the de-anonymizer in `evaluate`
    can substitute back into the LLM's reasoning before journaling.
    """
    aliases: dict[str, str] = {}
    counter = 0

    def alias_for(symbol: str) -> str:
        nonlocal counter
        for ph, orig in aliases.items():
            if orig == symbol:
                return ph
        ph = f"[ASSET_{counter}]"
        aliases[ph] = symbol
        counter += 1
        return ph

    new_symbol = alias_for(ctx.symbol)
    new_open = [alias_for(s) for s in ctx.open_positions]
    return (
        SignalContext(
            symbol=new_symbol,
            side=ctx.side,
            strategy=ctx.strategy,
            rule_confidence=ctx.rule_confidence,
            entry_price=ctx.entry_price,
            stop_price=ctx.stop_price,
            target_price=ctx.target_price,
            recent_bars=ctx.recent_bars[-MAX_CONTEXT_BARS:],
            regime=ctx.regime,
            insider_score=ctx.insider_score,
            news_headlines=ctx.news_headlines[:MAX_NEWS_HEADLINES],
            open_positions=new_open,
        ),
        aliases,
    )


def _build_user_prompt(ctx: SignalContext) -> str:
    """Render the context as deterministic JSON. Stable shape -> stable
    prompts -> better LLM behavior across providers."""
    # Auto-resolve the microstructure phase if the caller didn't pass one.
    # Keeping this lazy means SignalContext can stay a frozen dataclass and
    # callers don't need to compute the phase themselves.
    phase = ctx.phase or current_phase(datetime.now(UTC), ctx.asset_class)
    payload = {
        "candidate_signal": {
            "symbol": ctx.symbol,
            "side": ctx.side,
            "strategy": ctx.strategy,
            "rule_confidence": round(ctx.rule_confidence, 3),
            "entry": round(ctx.entry_price, 4),
            "stop": round(ctx.stop_price, 4),
            "target": round(ctx.target_price, 4) if ctx.target_price else None,
        },
        "context": {
            "regime": ctx.regime,
            "insider_score": ctx.insider_score,
            "news_headlines": ctx.news_headlines,
            "open_positions": ctx.open_positions,
            "recent_bars_count": len(ctx.recent_bars),
        },
        "market_phase": {
            "phase": phase,
            "posture": phase_posture(phase),
        },
    }
    return json.dumps(payload, default=str)


def _parse_verdict(raw: str) -> tuple[float, bool, str]:
    """Parse the LLM's JSON verdict. Defaults to identity on any parse
    failure — the journal still records the raw response for triage."""
    text = raw.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` if the LLM wrapped despite instructions.
        # Defensive against truncated / malformed fences (LLM hit max_tokens
        # mid-response, network drop) — fall through to identity rather than
        # let an IndexError or attribute lookup crash the agent loop.
        try:
            parts = text.split("```", 2)
            text = parts[1] if len(parts) >= 2 else parts[0]
            if text.startswith("json"):
                text = text[4:].lstrip()
            text = text.rsplit("```", 1)[0].strip()
        except (IndexError, ValueError, AttributeError):
            return 1.0, False, "fence parse failed; defaulting to identity"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return 1.0, False, "parse failed; defaulting to identity"
    if not isinstance(obj, dict):
        return 1.0, False, "verdict was not a JSON object"
    multiplier_raw = obj.get("multiplier", 1.0)
    halt_raw = obj.get("halt", False)
    reasoning = str(obj.get("reasoning", "")).strip() or "(no reasoning provided)"
    try:
        multiplier = float(multiplier_raw)
    except (TypeError, ValueError):
        multiplier = 1.0
    import math
    if not math.isfinite(multiplier):
        # NaN/inf -> identity
        multiplier = 1.0
    return multiplier, bool(halt_raw), reasoning


def _clamp(multiplier: float) -> float:
    """Hard-clip the LLM's multiplier into the safe band. NEVER trust an
    LLM-emitted number directly — a hallucinated 50x must clamp to 1.2."""
    import math
    if not math.isfinite(multiplier):  # NaN/inf guard
        return 1.0
    if multiplier < MULTIPLIER_FLOOR:
        return MULTIPLIER_FLOOR
    if multiplier > MULTIPLIER_CEILING:
        return MULTIPLIER_CEILING
    return multiplier


__all__ = [
    "MULTIPLIER_CEILING",
    "MULTIPLIER_FLOOR",
    "AutonomousReasoner",
    "SignalContext",
    "SignalJudgment",
]
