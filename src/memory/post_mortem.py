"""Generate a 1-paragraph LLM post-mortem narrative for a closed trade.

The post-mortem is the lesson the bot writes to itself after every trade
closes — "what was the setup, what happened, what's the lesson for next
time". It's the text we'll embed and recall later when an analogous setup
appears.

Failure mode: if the LLM is unavailable for any reason (rate limit, every
provider down, parse error), we fall back to a deterministic mechanical
summary so memory ingestion never blocks the runner. The mechanical PnL
data is the canonical record; the LLM lesson is enrichment.

Anonymization: the symbol of the closed trade and any tickers that were
open at entry are replaced with ``[ASSET_<n>]`` placeholders before the
prompt is sent to the LLM (Deep90 anti-bias pattern). The narrative is
de-anonymized before return, so the persisted MemoryStore record contains
real ticker names — that's what makes recall against future ``SPY``
setups actually find past ``SPY`` setups.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from src.llm import LLMUnavailableError, call_llm

logger = logging.getLogger(__name__)


# Threshold below which the trade counts as "breakeven" for label purposes.
# A 1-cent rounding error or a single-tick fill quirk shouldn't be promoted
# to a "win" or "loss" lesson; those are noise, not learning signal.
_BREAKEVEN_EPSILON_USD: float = 1.0

# Hard cap on the prompt we send. We don't include OHLCV here — the lesson
# is about decision quality at a high level, not bar-by-bar replay.
_MAX_NEWS_PEERS_IN_PROMPT: int = 5


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """Everything we know about a trade that has just closed.

    All fields are required at construction time except ``notes``; this
    keeps the post-mortem reproducible from the journal record alone — no
    "I'll back-fill the regime later" half-states.

    Attributes:
        trade_id: Stable identifier (ULID/uuid). Becomes the
            :class:`TradeMemory` primary key.
        entry_ts: Entry timestamp; tz-aware UTC.
        exit_ts: Exit timestamp; tz-aware UTC.
        symbol: Ticker.
        side: ``"buy"`` or ``"sell"``.
        strategy: Strategy name that produced the entry.
        entry_price: Fill price on entry.
        exit_price: Fill price on exit.
        qty: Position size in shares (can be fractional).
        stop_price: Stop level set at entry; may be ``None`` for
            no-stop entries (rare).
        target_price: Take-profit level; may be ``None``.
        pnl_usd: Realized P&L in USD.
        pnl_r: R-multiple (P&L / risk-per-share). ``None`` when stop is
            unknown so we can't compute it.
        holding_minutes: Wall-clock duration the position was open.
        setup_summary: 1-2 sentence description of why we entered. Comes
            from the strategy at entry-time and gets quoted into the
            post-mortem prompt.
        market_regime: ``"risk_on"`` / ``"risk_off"`` / ``"neutral"`` /
            ``None``.
        open_positions_at_entry: Tickers held when the trade was opened.
        notes: Free-form operator notes. Optional.
    """

    trade_id: str
    entry_ts: datetime
    exit_ts: datetime
    symbol: str
    side: str
    strategy: str
    entry_price: float
    exit_price: float
    qty: float
    stop_price: float | None
    target_price: float | None
    pnl_usd: float
    pnl_r: float | None
    holding_minutes: int
    setup_summary: str
    market_regime: str | None
    open_positions_at_entry: list[str]
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class PostMortem:
    """LLM-generated lesson + classification for a closed trade.

    Attributes:
        narrative: 1-paragraph lesson, real tickers (de-anonymized).
        label: ``"win"`` / ``"loss"`` / ``"breakeven"``.
        asof: ISO-8601 UTC timestamp of when the post-mortem was generated.
    """

    narrative: str
    label: str
    asof: str


_SYSTEM_PROMPT = (
    "You are a trading post-mortem writer for a paper-trading bot. The bot "
    "just closed a trade and wants ONE paragraph that will help its future "
    "self recognize and react better to similar setups. You see anonymized "
    "tickers ([ASSET_X]) so your training-data prior on the specific name "
    "cannot bias you.\n\n"
    "OUTPUT: exactly 3 sentences in 1 paragraph, plain text. No markdown, "
    "no quotation marks, no bullet points.\n"
    "  Sentence 1: what was the setup (1 sentence).\n"
    "  Sentence 2: what actually happened (1 sentence).\n"
    "  Sentence 3: the lesson for similar future setups (1 sentence, "
    "starts with 'Lesson:').\n\n"
    "Stay concrete. Reference the strategy, the regime, the time of day, "
    "or anything else in the input that materially affected the outcome. "
    "Do NOT invent facts not in the input."
)


def classify_label(pnl_usd: float, *, epsilon: float = _BREAKEVEN_EPSILON_USD) -> str:
    """Classify a closed trade's P&L into ``win`` / ``loss`` / ``breakeven``.

    Args:
        pnl_usd: Realized P&L in USD. Positive is a win, negative a loss.
        epsilon: Absolute USD threshold below which the trade is treated
            as breakeven. Default ``$1`` filters out single-cent rounding.

    Returns:
        One of ``"win"``, ``"loss"``, ``"breakeven"``.
    """
    if abs(pnl_usd) < epsilon:
        return "breakeven"
    if pnl_usd > 0:
        return "win"
    return "loss"


def _anonymize_trade(trade: ClosedTrade) -> tuple[ClosedTrade, dict[str, str]]:
    """Replace tickers in a :class:`ClosedTrade` with ``[ASSET_<n>]`` aliases.

    The mapping is keyed by placeholder so the de-anonymizer can substitute
    back into the LLM's narrative before storage.
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

    new_symbol = alias_for(trade.symbol)
    new_open = [alias_for(s) for s in trade.open_positions_at_entry[:_MAX_NEWS_PEERS_IN_PROMPT]]

    anon = ClosedTrade(
        trade_id=trade.trade_id,
        entry_ts=trade.entry_ts,
        exit_ts=trade.exit_ts,
        symbol=new_symbol,
        side=trade.side,
        strategy=trade.strategy,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        qty=trade.qty,
        stop_price=trade.stop_price,
        target_price=trade.target_price,
        pnl_usd=trade.pnl_usd,
        pnl_r=trade.pnl_r,
        holding_minutes=trade.holding_minutes,
        setup_summary=trade.setup_summary,
        market_regime=trade.market_regime,
        open_positions_at_entry=new_open,
        notes=trade.notes,
    )
    return anon, aliases


def _build_user_prompt(trade: ClosedTrade) -> str:
    """Render the trade as a stable JSON-ish block for the LLM."""
    payload = {
        "symbol": trade.symbol,  # already anonymized at this point
        "side": trade.side,
        "strategy": trade.strategy,
        "entry_price": round(trade.entry_price, 4),
        "exit_price": round(trade.exit_price, 4),
        "stop_price": (
            round(trade.stop_price, 4) if trade.stop_price is not None else None
        ),
        "target_price": (
            round(trade.target_price, 4) if trade.target_price is not None else None
        ),
        "pnl_usd": round(trade.pnl_usd, 2),
        "pnl_r": round(trade.pnl_r, 2) if trade.pnl_r is not None else None,
        "holding_minutes": trade.holding_minutes,
        "setup_summary": trade.setup_summary,
        "market_regime": trade.market_regime,
        "open_positions_at_entry": trade.open_positions_at_entry,
        "notes": trade.notes,
    }
    return json.dumps(payload, default=str)


def _mechanical_fallback(trade: ClosedTrade, *, label: str) -> str:
    """Deterministic 1-paragraph summary used when the LLM is unavailable.

    Uses real tickers (caller passes the un-anonymized trade) and quotes
    the strategy + R-multiple. Looks like::

        SPY long failed_breakout: -1.0R, stopped at 420.00 from 425.00 entry over 90 min.
    """
    side_word = "long" if trade.side == "buy" else "short"
    r_str = f"{trade.pnl_r:+.1f}R" if trade.pnl_r is not None else f"${trade.pnl_usd:+.0f}"
    stop_str = (
        f"stopped at {trade.stop_price:.2f} from {trade.entry_price:.2f} entry"
        if trade.stop_price is not None
        else f"closed at {trade.exit_price:.2f} from {trade.entry_price:.2f} entry"
    )
    return (
        f"{trade.symbol} {side_word} {trade.strategy}: {label} {r_str}, "
        f"{stop_str} over {trade.holding_minutes} min."
    )


def generate_post_mortem(trade: ClosedTrade, *, max_tokens: int = 200) -> PostMortem:
    """Generate a 1-paragraph lesson for a closed trade.

    Calls :func:`src.llm.call_llm` with a ticker-anonymized prompt, then
    de-anonymizes the resulting narrative before returning it.

    Args:
        trade: The closed-trade record.
        max_tokens: Cap on LLM output. Default 200, enough for ~3 short
            sentences, short enough that a runaway response is bounded.

    Returns:
        A :class:`PostMortem` with the (de-anonymized) narrative, the
        win/loss/breakeven label, and a generation timestamp.

    Failure mode:
        Any exception from ``call_llm`` (rate limit, parse, network) is
        caught and replaced with a deterministic mechanical summary —
        the post-mortem path must NOT crash the runner. The label is
        still computed from PnL even on fallback.
    """
    asof = datetime.now(UTC).isoformat()
    label = classify_label(trade.pnl_usd)

    try:
        anon_trade, aliases = _anonymize_trade(trade)
        user_prompt = _build_user_prompt(anon_trade)
        resp = call_llm(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=max_tokens,
            temperature=0.0,
        )
        narrative = resp.text.strip()
        # De-anonymize before storing so MemoryStore reads real tickers.
        for placeholder, original in aliases.items():
            narrative = narrative.replace(placeholder, original)
        if not narrative:
            # LLM returned empty body — treat as failure, fall back.
            raise LLMUnavailableError("LLM returned empty narrative")
        return PostMortem(narrative=narrative, label=label, asof=asof)
    except LLMUnavailableError as e:
        logger.warning("post_mortem: LLM unavailable, mechanical fallback: %s", e)
        return PostMortem(
            narrative=_mechanical_fallback(trade, label=label),
            label=label,
            asof=asof,
        )
    except Exception as e:
        # Defensive last resort: any other exception (parse, encode, etc.)
        # must not crash the runner. The mechanical summary is enough to
        # make the trade recallable; richer lessons can be regenerated later.
        logger.exception(
            "post_mortem: unexpected error, mechanical fallback (%s)", type(e).__name__
        )
        return PostMortem(
            narrative=_mechanical_fallback(trade, label=label),
            label=label,
            asof=asof,
        )


__all__ = [
    "ClosedTrade",
    "PostMortem",
    "classify_label",
    "generate_post_mortem",
]
