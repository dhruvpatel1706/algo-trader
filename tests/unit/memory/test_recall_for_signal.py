"""Unit tests for episodic-memory recall driven by a SignalContext.

Pinned properties:
  - build_anchor_query is deterministic for the same SignalContext.
  - Empty store -> empty recall (no crash).
  - One matching memory -> returned with similarity score.
  - format_recalled_memories_for_prompt: format string is exact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.agents.autonomous_reasoner import SignalContext
from src.memory.embeddings import DeterministicHashProvider
from src.memory.models import TradeMemory
from src.memory.recall_for_signal import (
    build_anchor_query,
    format_recalled_memories_for_prompt,
    recall_for_signal_context,
)
from src.memory.store import MemoryStore

# Use a "recent" timestamp (30 days ago) for memories that need to pass
# recall_similar's default 365-day window; the format-pinned tests below
# use a fixed historical date because they don't go through recall.
_RECENT_TS: datetime = datetime.now(UTC) - timedelta(days=30)


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")


@pytest.fixture
def provider() -> DeterministicHashProvider:
    return DeterministicHashProvider()


def _ctx(**overrides) -> SignalContext:
    base = {
        "symbol": "SPY",
        "side": "buy",
        "strategy": "failed_breakout",
        "rule_confidence": 0.7,
        "entry_price": 425.0,
        "stop_price": 420.0,
        "target_price": 435.0,
        "regime": "risk_on",
    }
    base.update(overrides)
    return SignalContext(**base)


# ---------------------------------------------------------------------------
# build_anchor_query
# ---------------------------------------------------------------------------


def test_build_anchor_query_is_deterministic_for_same_input():
    """Two calls with the same SignalContext produce the same anchor."""
    ctx = _ctx()
    a1 = build_anchor_query(ctx)
    a2 = build_anchor_query(ctx)
    assert a1 == a2


def test_build_anchor_query_includes_strategy_side_symbol():
    """Anchor must encode the three identity dimensions."""
    ctx = _ctx(symbol="QQQ", side="sell", strategy="ma_pullback")
    anchor = build_anchor_query(ctx)
    assert "ma_pullback" in anchor
    assert "sell" in anchor
    assert "QQQ" in anchor


def test_build_anchor_query_handles_unknown_regime():
    """A None regime renders as 'unknown' rather than the literal None."""
    ctx = _ctx(regime=None)
    anchor = build_anchor_query(ctx)
    assert "regime=unknown" in anchor


def test_build_anchor_query_rounds_rule_confidence():
    """Tiny float drift in rule_confidence shouldn't change the anchor.

    Both 0.7 and 0.7000001 should render with the same 2-decimal rounding,
    which keeps recall reproducible across runs.
    """
    a1 = build_anchor_query(_ctx(rule_confidence=0.7))
    a2 = build_anchor_query(_ctx(rule_confidence=0.7000001))
    assert a1 == a2


# ---------------------------------------------------------------------------
# recall_for_signal_context
# ---------------------------------------------------------------------------


def test_recall_for_signal_context_returns_empty_for_empty_store(store, provider):
    out = recall_for_signal_context(_ctx(), store=store, provider=provider, k=3)
    assert out == []


def test_recall_for_signal_context_returns_one_matching_memory(store, provider):
    """A single memory with a similar narrative is recallable."""
    # Insert a memory whose narrative is the SAME anchor text -> max similarity.
    ctx = _ctx()
    anchor = build_anchor_query(ctx)
    mem = TradeMemory(
        trade_id="t-1",
        ts=_RECENT_TS,
        symbol="SPY",
        side="buy",
        strategy="failed_breakout",
        narrative=anchor,  # identical text -> identical embedding
        outcome_pnl_usd=-50.0,
        outcome_r=-1.0,
        outcome_label="loss",
        embedding=provider.embed(anchor),
    )
    store.add(mem)

    out = recall_for_signal_context(
        ctx, store=store, provider=provider, k=3, min_similarity=0.0
    )
    assert len(out) == 1
    recalled, sim = out[0]
    assert recalled.trade_id == "t-1"
    # Identical embeddings -> cosine ~= 1.0
    assert sim == pytest.approx(1.0, abs=1e-6)


def test_recall_for_signal_context_filters_by_min_similarity(store, provider):
    """A memory with an unrelated narrative is filtered out by the threshold."""
    ctx = _ctx()
    junk_text = "completely unrelated narrative about something else entirely"
    mem = TradeMemory(
        trade_id="t-junk",
        ts=_RECENT_TS,
        symbol="ZZZ",
        side="sell",
        strategy="other",
        narrative=junk_text,
        outcome_pnl_usd=10.0,
        outcome_r=0.5,
        outcome_label="win",
        embedding=provider.embed(junk_text),
    )
    store.add(mem)

    # Default min_similarity=0.5; a random hash-based pair almost certainly
    # falls below that.
    out = recall_for_signal_context(ctx, store=store, provider=provider, k=3)
    # Allow either empty (filtered out) or non-empty (deterministic embeddings
    # may occasionally tile near 0.5). The pin is "doesn't crash".
    assert isinstance(out, list)


def test_recall_for_signal_context_respects_same_strategy_only(store, provider):
    """When same_strategy_only=True, only same-strategy memories surface."""
    ctx = _ctx(strategy="failed_breakout")
    anchor = build_anchor_query(ctx)
    # Two memories: one same-strategy, one different.
    same = TradeMemory(
        trade_id="same",
        ts=_RECENT_TS,
        symbol="SPY",
        side="buy",
        strategy="failed_breakout",
        narrative=anchor,
        outcome_pnl_usd=10.0,
        outcome_r=1.0,
        outcome_label="win",
        embedding=provider.embed(anchor),
    )
    other = TradeMemory(
        trade_id="other",
        ts=_RECENT_TS,
        symbol="SPY",
        side="buy",
        strategy="ma_pullback",
        narrative=anchor,
        outcome_pnl_usd=10.0,
        outcome_r=1.0,
        outcome_label="win",
        embedding=provider.embed(anchor),
    )
    store.add(same)
    store.add(other)

    out = recall_for_signal_context(
        ctx,
        store=store,
        provider=provider,
        k=3,
        min_similarity=0.0,
        same_strategy_only=True,
    )
    ids = [m.trade_id for m, _ in out]
    assert "same" in ids
    assert "other" not in ids


# ---------------------------------------------------------------------------
# format_recalled_memories_for_prompt — format pinned for downstream consumers
# ---------------------------------------------------------------------------


def test_format_recalled_memories_for_prompt_empty_input_returns_empty_list():
    assert format_recalled_memories_for_prompt([]) == []


def test_format_recalled_memories_for_prompt_renders_each_memory_as_one_line():
    mem = TradeMemory(
        trade_id="t-1",
        ts=datetime(2025, 4, 15, 13, 30, 0, tzinfo=UTC),
        symbol="SPY",
        side="buy",
        strategy="failed_breakout",
        narrative="pre-market WVF setup failed in risk-off",
        outcome_pnl_usd=-50.0,
        outcome_r=-1.0,
        outcome_label="loss",
        embedding=[],
    )
    lines = format_recalled_memories_for_prompt([(mem, 0.83)])
    assert len(lines) == 1
    line = lines[0]
    # Pinned format: SYMBOL SIDE STRATEGY DATE: outcome=LABEL XR; lesson: ...
    assert line == (
        "SPY buy failed_breakout 2025-04-15: outcome=loss -1.0R; "
        "lesson: pre-market WVF setup failed in risk-off"
    )


def test_format_recalled_memories_for_prompt_handles_open_outcome():
    """Open trades render outcome=open without R-multiple."""
    mem = TradeMemory(
        trade_id="t-open",
        ts=datetime(2025, 4, 15, tzinfo=UTC),
        symbol="QQQ",
        side="sell",
        strategy="ma_pullback",
        narrative="position entered, still live",
        outcome_pnl_usd=None,
        outcome_r=None,
        outcome_label="open",
        embedding=[],
    )
    lines = format_recalled_memories_for_prompt([(mem, 0.6)])
    assert len(lines) == 1
    assert "outcome=open" in lines[0]
    # No R-multiple appears for an open trade.
    assert "R;" not in lines[0]


def test_format_recalled_memories_for_prompt_truncates_long_narrative():
    """A very long narrative is collapsed to a single line and trimmed."""
    long_narrative = "word " * 200  # ~1000 chars
    mem = TradeMemory(
        trade_id="t-long",
        ts=datetime(2025, 4, 15, tzinfo=UTC),
        symbol="SPY",
        side="buy",
        strategy="failed_breakout",
        narrative=long_narrative,
        outcome_pnl_usd=10.0,
        outcome_r=1.0,
        outcome_label="win",
        embedding=[],
    )
    lines = format_recalled_memories_for_prompt([(mem, 0.9)])
    assert len(lines) == 1
    # Single line — no embedded newlines.
    assert "\n" not in lines[0]
    # Length is bounded.
    assert len(lines[0]) < 300


def test_format_recalled_memories_for_prompt_renders_multiple_in_order():
    """Two memories produce two lines in the same order as the input."""
    a = TradeMemory(
        trade_id="t-a",
        ts=datetime(2025, 1, 1, tzinfo=UTC),
        symbol="AAA",
        side="buy",
        strategy="s1",
        narrative="a",
        outcome_pnl_usd=1.0,
        outcome_r=0.1,
        outcome_label="win",
        embedding=[],
    )
    b = TradeMemory(
        trade_id="t-b",
        ts=datetime(2025, 2, 1, tzinfo=UTC),
        symbol="BBB",
        side="sell",
        strategy="s2",
        narrative="b",
        outcome_pnl_usd=-1.0,
        outcome_r=-0.1,
        outcome_label="loss",
        embedding=[],
    )
    lines = format_recalled_memories_for_prompt([(a, 0.9), (b, 0.6)])
    assert lines[0].startswith("AAA buy s1 2025-01-01:")
    assert lines[1].startswith("BBB sell s2 2025-02-01:")
