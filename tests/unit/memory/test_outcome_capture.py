"""Unit tests for :class:`OutcomeCapture` — closed-trade ingestion.

Pinned properties:
  - record(trade) creates a TradeMemory with the right primary fields.
  - Idempotency: a second record() with the same trade_id is a no-op
    (logs a warning, returns the existing row).
  - Best-effort: post-mortem failure -> mechanical fallback narrative,
    embed failure -> empty embedding (still persists), store failure ->
    None (logged, no crash).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from src.memory.embeddings import DeterministicHashProvider
from src.memory.outcome_capture import OutcomeCapture
from src.memory.post_mortem import ClosedTrade
from src.memory.store import MemoryStore, MemoryStoreError


def _llm_response(text: str, provider: str = "anthropic"):
    from src.llm.router import LLMResponse

    return LLMResponse(text=text, provider=provider, model="test", elapsed_ms=12)


def _trade(**overrides) -> ClosedTrade:
    base = {
        "trade_id": "01HZZZ-loss-1",
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
        "open_positions_at_entry": [],
    }
    base.update(overrides)
    return ClosedTrade(**base)


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")


@pytest.fixture
def provider() -> DeterministicHashProvider:
    return DeterministicHashProvider()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_record_persists_trade_memory_with_right_fields(store, provider):
    """record() should write a row whose primary fields mirror the closed trade."""
    capture = OutcomeCapture(store=store, provider=provider)
    trade = _trade(trade_id="t-happy")

    with patch(
        "src.memory.post_mortem.call_llm",
        return_value=_llm_response(
            "Entered SPY long failed_breakout. "
            "Stopped out at -1R as regime stayed risk_on but "
            "internals weakened. Lesson: confirm internals before fading."
        ),
    ):
        out = capture.record(trade)

    assert out is not None
    assert out.trade_id == "t-happy"
    assert out.symbol == "SPY"
    assert out.side == "buy"
    assert out.strategy == "failed_breakout"
    assert out.outcome_pnl_usd == -50.0
    assert out.outcome_r == -1.0
    assert out.outcome_label == "loss"
    # Embedding has the provider's dimensionality.
    assert len(out.embedding) == provider.dim

    # Persistence: the row is recoverable from the store.
    persisted = store.get("t-happy")
    assert persisted is not None
    assert persisted.narrative == out.narrative


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_record_is_idempotent_on_trade_id(store, provider, caplog):
    """Second record() with same trade_id -> no-op + warning."""
    capture = OutcomeCapture(store=store, provider=provider)
    trade = _trade(trade_id="t-dup")

    with patch(
        "src.memory.post_mortem.call_llm",
        return_value=_llm_response("a. b. Lesson: c."),
    ):
        first = capture.record(trade)
        with caplog.at_level(logging.WARNING, logger="src.memory.outcome_capture"):
            second = capture.record(trade)

    assert first is not None
    assert second is not None
    # Same row reference (by content): trade_id + narrative match.
    assert second.trade_id == first.trade_id
    assert second.narrative == first.narrative
    # Warning emitted that we skipped.
    assert any(
        "already in memory" in rec.message and "t-dup" in rec.message
        for rec in caplog.records
    )
    # Only one row in the store, not two.
    assert store.count() == 1


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_record_uses_mechanical_fallback_when_post_mortem_raises(store, provider):
    """If generate_post_mortem itself raises, we still create a TradeMemory.

    generate_post_mortem is supposed to be fail-safe internally, but if a
    bug ever lets an exception escape, OutcomeCapture must still produce
    a usable mechanical summary so the trade isn't lost from memory.
    """
    capture = OutcomeCapture(store=store, provider=provider)
    trade = _trade(trade_id="t-mortem-blew-up")

    with patch(
        "src.memory.outcome_capture.generate_post_mortem",
        side_effect=RuntimeError("post-mortem internal explosion"),
    ):
        out = capture.record(trade)

    assert out is not None
    assert out.trade_id == "t-mortem-blew-up"
    # Mechanical fallback narrative mentions the strategy and the R-multiple.
    assert "failed_breakout" in out.narrative
    assert "loss" in out.narrative.lower()
    assert out.outcome_label == "loss"


def test_record_persists_with_empty_embedding_when_provider_raises(store, provider):
    """Embed failure -> persist with empty embedding rather than skip the trade."""
    capture = OutcomeCapture(store=store, provider=provider)
    trade = _trade(trade_id="t-no-embed")

    with (
        patch(
            "src.memory.post_mortem.call_llm",
            return_value=_llm_response("a. b. Lesson: c."),
        ),
        patch.object(provider, "embed", side_effect=RuntimeError("embed down")),
    ):
        out = capture.record(trade)

    assert out is not None
    assert out.embedding == []
    # Still persisted in the store.
    persisted = store.get("t-no-embed")
    assert persisted is not None


def test_record_returns_none_when_store_add_fails(store, provider, caplog):
    """Store write failure must NOT crash; it logs and returns None."""
    capture = OutcomeCapture(store=store, provider=provider)
    trade = _trade(trade_id="t-store-fail")

    with (
        patch(
            "src.memory.post_mortem.call_llm",
            return_value=_llm_response("a. b. Lesson: c."),
        ),
        patch.object(store, "add", side_effect=MemoryStoreError("disk full")),
        caplog.at_level(logging.ERROR, logger="src.memory.outcome_capture"),
    ):
        out = capture.record(trade)

    assert out is None
    assert any("store write failed" in rec.message for rec in caplog.records)


def test_record_returns_none_when_store_add_raises_unexpected(store, provider, caplog):
    """Any non-MemoryStoreError from store.add must also be swallowed."""
    capture = OutcomeCapture(store=store, provider=provider)
    trade = _trade(trade_id="t-store-weird")

    with (
        patch(
            "src.memory.post_mortem.call_llm",
            return_value=_llm_response("a. b. Lesson: c."),
        ),
        patch.object(store, "add", side_effect=OSError("unexpected")),
        caplog.at_level(logging.ERROR, logger="src.memory.outcome_capture"),
    ):
        out = capture.record(trade)

    assert out is None


def test_record_handles_mechanical_post_mortem_when_llm_unavailable(store, provider):
    """End-to-end: LLM down -> mechanical PostMortem -> still embeds + stores."""
    from src.llm import LLMUnavailableError

    capture = OutcomeCapture(store=store, provider=provider)
    trade = _trade(trade_id="t-llm-down")

    with patch(
        "src.memory.post_mortem.call_llm",
        side_effect=LLMUnavailableError("all providers down"),
    ):
        out = capture.record(trade)

    assert out is not None
    # Mechanical narrative is non-empty and embedded.
    assert out.narrative
    assert len(out.embedding) == provider.dim


def test_record_uses_classify_label_for_breakeven(store, provider):
    """A near-zero PnL trade is labeled 'breakeven', not 'win'/'loss'."""
    capture = OutcomeCapture(store=store, provider=provider)
    trade = _trade(trade_id="t-be", pnl_usd=0.0, pnl_r=0.0)

    with patch(
        "src.memory.post_mortem.call_llm",
        return_value=_llm_response("a. b. Lesson: c."),
    ):
        out = capture.record(trade)

    assert out is not None
    assert out.outcome_label == "breakeven"


def test_record_returns_existing_row_on_idempotent_replay(store, provider):
    """Replay-safety: second call returns the stored TradeMemory unchanged.

    This is what the runner depends on when broker fill events get
    replayed during recovery — the in-memory and on-disk records remain
    consistent and the post-mortem isn't regenerated (which would have
    cost an LLM call for nothing).
    """
    capture = OutcomeCapture(store=store, provider=provider)
    trade = _trade(trade_id="t-replay")

    call_count = {"n": 0}

    def fake_call(*, system, user, max_tokens, temperature):
        call_count["n"] += 1
        return _llm_response("a. b. Lesson: c.")

    with patch("src.memory.post_mortem.call_llm", side_effect=fake_call):
        first = capture.record(trade)
        second = capture.record(trade)

    assert first is not None
    assert second is not None
    # Only one LLM call happened across the two record() invocations.
    assert call_count["n"] == 1
