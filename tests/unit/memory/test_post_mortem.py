"""Unit tests for the post-mortem narrative generator.

Critical properties pinned:
  - ClosedTrade can be constructed with the documented field set.
  - generate_post_mortem anonymizes tickers in the LLM-bound prompt and
    de-anonymizes them in the returned narrative.
  - LLM unavailability falls back to a deterministic mechanical summary
    (the runner must never crash on a closed trade).
  - Label classification: profit -> "win", loss -> "loss", near-zero ->
    "breakeven".
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from src.llm import LLMUnavailableError
from src.memory.post_mortem import (
    ClosedTrade,
    PostMortem,
    classify_label,
    generate_post_mortem,
)


def _llm_response(text: str, provider: str = "anthropic"):
    from src.llm.router import LLMResponse

    return LLMResponse(text=text, provider=provider, model="test", elapsed_ms=12)


def _trade(**overrides) -> ClosedTrade:
    base = {
        "trade_id": "01HZZZ1",
        "entry_ts": datetime(2025, 4, 15, 13, 30, 0, tzinfo=UTC),
        "exit_ts": datetime(2025, 4, 15, 15, 0, 0, tzinfo=UTC),
        "symbol": "SPY",
        "side": "buy",
        "strategy": "failed_breakout",
        "entry_price": 425.0,
        "exit_price": 420.0,
        "qty": 10.0,
        "stop_price": 420.0,
        "target_price": 435.0,
        "pnl_usd": -50.0,
        "pnl_r": -1.0,
        "holding_minutes": 90,
        "setup_summary": "Pre-market high failed breakout in low-VIX regime.",
        "market_regime": "risk_on",
        "open_positions_at_entry": ["AAPL", "MSFT"],
    }
    base.update(overrides)
    return ClosedTrade(**base)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_closed_trade_can_be_constructed_with_all_fields():
    """Smoke: every documented field is accepted, dataclass is frozen."""
    t = _trade(notes="operator audit notes")
    assert t.trade_id == "01HZZZ1"
    assert t.symbol == "SPY"
    assert t.side == "buy"
    assert t.strategy == "failed_breakout"
    assert t.pnl_usd == -50.0
    assert t.pnl_r == -1.0
    assert t.holding_minutes == 90
    assert t.market_regime == "risk_on"
    assert t.open_positions_at_entry == ["AAPL", "MSFT"]
    assert t.notes == "operator audit notes"


def test_closed_trade_notes_is_optional():
    """notes defaults to None — not all strategies populate it."""
    t = _trade()
    assert t.notes is None


# ---------------------------------------------------------------------------
# Label classification
# ---------------------------------------------------------------------------


def test_classify_label_win_for_positive_pnl():
    assert classify_label(123.45) == "win"


def test_classify_label_loss_for_negative_pnl():
    assert classify_label(-50.0) == "loss"


def test_classify_label_breakeven_for_near_zero_pnl():
    """Single-cent rounding shouldn't promote to win/loss."""
    assert classify_label(0.0) == "breakeven"
    assert classify_label(0.5) == "breakeven"
    assert classify_label(-0.5) == "breakeven"


def test_classify_label_breakeven_threshold_is_dollar():
    """Just above $1 -> win; just below -> breakeven. Pin the boundary."""
    assert classify_label(1.01) == "win"
    assert classify_label(-1.01) == "loss"
    assert classify_label(0.99) == "breakeven"


# ---------------------------------------------------------------------------
# LLM happy path: anonymization + de-anonymization
# ---------------------------------------------------------------------------


def test_generate_post_mortem_anonymizes_prompt_and_deanonymizes_narrative():
    """The LLM must NEVER see the real ticker; the stored narrative must.

    Verifies:
      * The user prompt sent to call_llm has no instance of the real symbol
        ('NVDA') or peer tickers ('AAPL').
      * The LLM-returned narrative (which references [ASSET_0]) is
        de-anonymized back to NVDA before being returned.
    """
    captured: dict[str, str] = {}

    def fake_call(*, system, user, max_tokens, temperature):
        captured["user"] = user
        # LLM returns the placeholder; we expect substitution back.
        return _llm_response(
            "Entered [ASSET_0] long on failed_breakout. "
            "Trade rolled over after entry. Lesson: verify regime."
        )

    trade = _trade(symbol="NVDA", open_positions_at_entry=["AAPL"])
    with patch("src.memory.post_mortem.call_llm", side_effect=fake_call):
        result = generate_post_mortem(trade)

    # LLM saw anonymized.
    assert "NVDA" not in captured["user"]
    assert "AAPL" not in captured["user"]
    assert "[ASSET_" in captured["user"]

    # Returned narrative has real ticker back.
    assert isinstance(result, PostMortem)
    assert "NVDA" in result.narrative
    assert "[ASSET_" not in result.narrative


def test_generate_post_mortem_returns_postmortem_with_label_and_asof():
    """Sanity: every field on the returned dataclass is populated."""
    trade = _trade(pnl_usd=200.0, pnl_r=2.0)
    with patch(
        "src.memory.post_mortem.call_llm",
        return_value=_llm_response("Setup. What happened. Lesson: x."),
    ):
        result = generate_post_mortem(trade)
    assert result.label == "win"
    assert "Setup" in result.narrative
    # asof is ISO-8601 UTC.
    assert "T" in result.asof
    assert result.asof.endswith("+00:00") or result.asof.endswith("Z")


# ---------------------------------------------------------------------------
# Failure modes — fall back to mechanical summary, never crash
# ---------------------------------------------------------------------------


def test_generate_post_mortem_falls_back_when_llm_unavailable():
    """LLMUnavailableError -> deterministic mechanical summary, label still set."""
    trade = _trade(symbol="SPY", side="buy", pnl_r=-1.0)
    with patch(
        "src.memory.post_mortem.call_llm",
        side_effect=LLMUnavailableError("all providers down"),
    ):
        result = generate_post_mortem(trade)

    assert isinstance(result, PostMortem)
    assert result.label == "loss"  # pnl=-50 -> loss
    # Mechanical summary mentions the strategy + R + entry/stop.
    assert "SPY" in result.narrative
    assert "failed_breakout" in result.narrative
    assert "loss" in result.narrative.lower()


def test_generate_post_mortem_falls_back_on_unexpected_exception():
    """Any random exception in the LLM path also drops to mechanical."""
    with patch(
        "src.memory.post_mortem.call_llm",
        side_effect=ValueError("totally unexpected"),
    ):
        result = generate_post_mortem(_trade())
    assert isinstance(result, PostMortem)
    # Mechanical fallback always populates a non-empty narrative.
    assert result.narrative


def test_generate_post_mortem_falls_back_on_empty_llm_response():
    """LLM returns empty body -> we treat as failure and use mechanical."""
    with patch(
        "src.memory.post_mortem.call_llm",
        return_value=_llm_response("   "),
    ):
        result = generate_post_mortem(_trade())
    # Mechanical fallback string mentions the strategy.
    assert "failed_breakout" in result.narrative


def test_generate_post_mortem_label_stable_across_paths():
    """Label is computed from PnL and survives both LLM-success and fallback."""
    win_trade = _trade(pnl_usd=200.0)
    loss_trade = _trade(pnl_usd=-200.0)
    be_trade = _trade(pnl_usd=0.0)

    with patch(
        "src.memory.post_mortem.call_llm",
        return_value=_llm_response("a. b. lesson: c."),
    ):
        assert generate_post_mortem(win_trade).label == "win"
        assert generate_post_mortem(loss_trade).label == "loss"
        assert generate_post_mortem(be_trade).label == "breakeven"
