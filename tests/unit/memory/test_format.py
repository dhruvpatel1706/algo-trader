"""Unit tests for :func:`format_memories_for_prompt`."""

from __future__ import annotations

from datetime import UTC, datetime

from src.memory.format import format_memories_for_prompt
from src.memory.models import TradeMemory


def _mem(
    *,
    trade_id: str = "t-1",
    ts: datetime | None = None,
    symbol: str = "SPY",
    side: str = "buy",
    strategy: str = "failed_breakout",
    narrative: str = "rejection wick after pre-market gap up",
    outcome_pnl_usd: float | None = 340.0,
    outcome_r: float | None = 1.4,
    outcome_label: str | None = "win",
) -> TradeMemory:
    if ts is None:
        ts = datetime(2025, 3, 12, tzinfo=UTC)
    return TradeMemory(
        trade_id=trade_id,
        ts=ts,
        symbol=symbol,
        side=side,
        strategy=strategy,
        narrative=narrative,
        outcome_pnl_usd=outcome_pnl_usd,
        outcome_r=outcome_r,
        outcome_label=outcome_label,
        embedding=[0.0, 1.0],
    )


def test_format_empty_returns_placeholder_message():
    out = format_memories_for_prompt([])
    assert out == "## Similar past trades:\nNo similar prior trades in memory."


def test_format_single_win_renders_expected_shape():
    mem = _mem()
    out = format_memories_for_prompt([(mem, 0.84)])
    lines = out.splitlines()
    assert lines[0] == "## Similar past trades:"
    assert len(lines) == 2
    line = lines[1]
    assert line.startswith("1. [SPY buy 2025-03-12, similarity 0.84]")
    assert "failed_breakout" in line
    assert "rejection wick after pre-market gap up" in line
    assert "WIN +1.4R (+$340)" in line


def test_format_loss_uses_negative_signs():
    mem = _mem(
        outcome_pnl_usd=-220.0,
        outcome_r=-1.0,
        outcome_label="loss",
    )
    out = format_memories_for_prompt([(mem, 0.78)])
    assert "LOSS -1.0R (-$220)" in out


def test_format_open_trade_renders_open_token():
    mem = _mem(
        outcome_pnl_usd=None,
        outcome_r=None,
        outcome_label="open",
    )
    out = format_memories_for_prompt([(mem, 0.55)])
    assert "outcome: OPEN" in out
    # No P&L appended when outcome is unknown.
    assert "$" not in out.split("outcome:", 1)[1]


def test_format_missing_label_falls_back_to_open():
    mem = _mem(
        outcome_pnl_usd=None,
        outcome_r=None,
        outcome_label=None,
    )
    out = format_memories_for_prompt([(mem, 0.5)])
    assert "outcome: OPEN" in out


def test_format_truncates_at_max_items():
    mems = [(_mem(trade_id=f"t-{i}"), 0.9 - i * 0.05) for i in range(10)]
    out = format_memories_for_prompt(mems, max_items=3)
    lines = out.splitlines()
    # 1 header + 3 items.
    assert len(lines) == 4
    assert lines[1].startswith("1.")
    assert lines[2].startswith("2.")
    assert lines[3].startswith("3.")


def test_format_long_narrative_is_trimmed_with_ellipsis():
    long_text = "a " * 500  # well above the cap
    mem = _mem(narrative=long_text)
    out = format_memories_for_prompt([(mem, 0.7)])
    # Long narrative should be capped — the rendered string is much shorter
    # than the input even after the boilerplate prefix.
    assert len(out) < len(long_text)
    assert "…" in out


def test_format_collapses_internal_whitespace_in_narrative():
    mem = _mem(narrative="line one\n\n\tline two   with spaces")
    out = format_memories_for_prompt([(mem, 0.6)])
    assert "line one line two with spaces" in out
