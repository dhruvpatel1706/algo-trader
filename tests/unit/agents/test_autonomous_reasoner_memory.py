"""Tests for the AutonomousReasoner ↔ episodic-memory integration.

Pinned properties:
  - Reasoner with memory_store=None behaves identically to the legacy
    reasoner (smoke).
  - Reasoner with memory_store + provider feeds recalled memories into
    the user prompt under "recalled_memories".
  - Recall failure is best-effort: judgment still produced,
    "recalled_memories" empty in prompt.
  - Recalled memories are anonymized — the LLM never sees real tickers
    even via the recalled-memory lines.
  - End-to-end: capture an outcome -> recall it -> the (anonymized)
    narrative shows up in a subsequent prompt.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from src.agents.autonomous_reasoner import AutonomousReasoner, SignalContext
from src.memory.embeddings import DeterministicHashProvider
from src.memory.outcome_capture import OutcomeCapture
from src.memory.post_mortem import ClosedTrade
from src.memory.store import MemoryStore

# Memories must be within the recall_similar default 365-day window to
# surface; use a recent timestamp.
_RECENT_TS: datetime = datetime.now(UTC) - timedelta(days=30)


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


def _llm_response(text: str, provider: str = "anthropic"):
    from src.llm.router import LLMResponse

    return LLMResponse(text=text, provider=provider, model="test", elapsed_ms=12)


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")


@pytest.fixture
def provider() -> DeterministicHashProvider:
    return DeterministicHashProvider()


# ---------------------------------------------------------------------------
# Backward compatibility (memory not configured)
# ---------------------------------------------------------------------------


def test_reasoner_with_no_memory_store_behaves_unchanged():
    """Default reasoner construction works exactly as before; recall is no-op."""
    verdict = json.dumps({"multiplier": 0.9, "halt": False, "reasoning": "ok"})
    captured: dict[str, str] = {}

    def fake_call(*, system, user, max_tokens, temperature):
        captured["user"] = user
        return _llm_response(verdict)

    r = AutonomousReasoner()
    with patch("src.agents.autonomous_reasoner.call_llm", side_effect=fake_call):
        out = r.evaluate(_ctx())

    assert out.multiplier == 0.9
    payload = json.loads(captured["user"])
    # New field exists and is empty for the no-memory case.
    assert payload["recalled_memories"] == []


# ---------------------------------------------------------------------------
# Memory wiring
# ---------------------------------------------------------------------------


def test_reasoner_with_memory_includes_recalled_lines_in_prompt(store, provider):
    """When recall returns hits, they appear in the user prompt under
    `recalled_memories`."""
    # Pre-populate the store with a memory that will match.
    from src.memory.models import TradeMemory
    from src.memory.recall_for_signal import build_anchor_query

    ctx = _ctx()
    anchor = build_anchor_query(ctx)
    mem = TradeMemory(
        trade_id="t-prior",
        ts=_RECENT_TS,
        symbol="SPY",
        side="buy",
        strategy="failed_breakout",
        narrative="pre-market WVF setups failed in risk-off",
        outcome_pnl_usd=-50.0,
        outcome_r=-1.0,
        outcome_label="loss",
        embedding=provider.embed(anchor),  # match the anchor exactly -> high sim
    )
    store.add(mem)

    verdict = json.dumps({"multiplier": 0.7, "halt": False, "reasoning": "memory says weak"})
    captured: dict[str, str] = {}

    def fake_call(*, system, user, max_tokens, temperature):
        captured["user"] = user
        return _llm_response(verdict)

    r = AutonomousReasoner(
        memory_store=store,
        embedding_provider=provider,
        recall_min_similarity=0.0,
    )
    with patch("src.agents.autonomous_reasoner.call_llm", side_effect=fake_call):
        out = r.evaluate(ctx)

    assert out.multiplier == 0.7
    payload = json.loads(captured["user"])
    assert "recalled_memories" in payload
    recalled = payload["recalled_memories"]
    assert isinstance(recalled, list)
    assert len(recalled) >= 1
    # Each recalled line carries the strategy + outcome.
    assert any("failed_breakout" in line and "loss" in line for line in recalled)


def test_reasoner_anonymizes_real_ticker_in_recalled_lines(store, provider):
    """The LLM never sees the real ticker — even via the recalled-memory
    lines, which are sourced from the (un-anonymized) memory store."""
    from src.memory.models import TradeMemory
    from src.memory.recall_for_signal import build_anchor_query

    ctx = _ctx(symbol="NVDA")
    anchor = build_anchor_query(ctx)
    mem = TradeMemory(
        trade_id="t-prior-nvda",
        ts=_RECENT_TS,
        symbol="NVDA",
        side="buy",
        strategy="failed_breakout",
        narrative="NVDA failed_breakout flopped after entry",
        outcome_pnl_usd=-100.0,
        outcome_r=-1.5,
        outcome_label="loss",
        embedding=provider.embed(anchor),
    )
    store.add(mem)

    verdict = json.dumps({"multiplier": 0.8, "halt": False, "reasoning": "ok"})
    captured: dict[str, str] = {}

    def fake_call(*, system, user, max_tokens, temperature):
        captured["user"] = user
        return _llm_response(verdict)

    r = AutonomousReasoner(
        memory_store=store, embedding_provider=provider, recall_min_similarity=0.0
    )
    with patch("src.agents.autonomous_reasoner.call_llm", side_effect=fake_call):
        r.evaluate(ctx)

    user_prompt_text = captured["user"]
    # The real ticker must not appear ANYWHERE in the LLM-bound prompt,
    # neither in candidate_signal nor in recalled_memories.
    assert "NVDA" not in user_prompt_text
    # And the placeholder is present.
    assert "[ASSET_" in user_prompt_text


def test_reasoner_recall_failure_is_swallowed_and_judgment_proceeds(store, provider):
    """If recall raises, the reasoner logs and continues with empty memories."""
    verdict = json.dumps({"multiplier": 1.0, "halt": False, "reasoning": "no recall"})
    captured: dict[str, str] = {}

    def fake_call(*, system, user, max_tokens, temperature):
        captured["user"] = user
        return _llm_response(verdict)

    r = AutonomousReasoner(memory_store=store, embedding_provider=provider)
    with (
        patch(
            "src.memory.recall_for_signal.recall_for_signal_context",
            side_effect=RuntimeError("recall blew up"),
        ),
        patch("src.agents.autonomous_reasoner.call_llm", side_effect=fake_call),
    ):
        out = r.evaluate(_ctx())

    # Judgment still produced (fail-soft).
    assert out.multiplier == 1.0
    payload = json.loads(captured["user"])
    # And recalled_memories is the empty fallback.
    assert payload["recalled_memories"] == []


def test_reasoner_recall_only_one_arg_supplied_skips_recall(store, provider):
    """memory_store without provider (or vice versa) => recall is skipped.

    Both must be set for recall to run; partial config should not raise.
    """
    verdict = json.dumps({"multiplier": 1.0, "halt": False, "reasoning": "x"})
    captured: dict[str, str] = {}

    def fake_call(*, system, user, max_tokens, temperature):
        captured["user"] = user
        return _llm_response(verdict)

    r1 = AutonomousReasoner(memory_store=store)  # no provider
    r2 = AutonomousReasoner(embedding_provider=provider)  # no store

    with patch("src.agents.autonomous_reasoner.call_llm", side_effect=fake_call):
        r1.evaluate(_ctx())
        payload1 = json.loads(captured["user"])
        r2.evaluate(_ctx())
        payload2 = json.loads(captured["user"])

    assert payload1["recalled_memories"] == []
    assert payload2["recalled_memories"] == []


# ---------------------------------------------------------------------------
# End-to-end: capture -> recall
# ---------------------------------------------------------------------------


def test_end_to_end_capture_then_recall_surfaces_lesson_in_prompt(store, provider):
    """An ingested closed trade should be recallable and appear in the
    next signal evaluation's prompt under recalled_memories.

    This is the headline contract — the bot learns from a previous loss
    when evaluating a similar setup.
    """
    # Step 1: simulate a prior trade closing and being captured.
    capture = OutcomeCapture(store=store, provider=provider)
    prior_trade = ClosedTrade(
        trade_id="prior-1",
        entry_ts=_RECENT_TS,
        exit_ts=_RECENT_TS + timedelta(hours=1),
        symbol="SPY",
        side="buy",
        strategy="failed_breakout",
        entry_price=420.0,
        exit_price=415.0,
        qty=10.0,
        stop_price=415.0,
        target_price=430.0,
        pnl_usd=-50.0,
        pnl_r=-1.0,
        holding_minutes=90,
        setup_summary="Pre-market high failed breakout in low-VIX regime.",
        market_regime="risk_on",
        open_positions_at_entry=[],
    )
    # Use a deterministic LLM stand-in for the post-mortem.
    pm_text = (
        "SPY long failed_breakout in pre-market with high WVF. "
        "Trade went sideways then stopped out at -1R. "
        "Lesson: confirm regime persistence before fading premarket setups."
    )
    with patch(
        "src.memory.post_mortem.call_llm",
        return_value=_llm_response(pm_text),
    ):
        captured_mem = capture.record(prior_trade)
    assert captured_mem is not None

    # Step 2: a new analogous signal arrives. We expect the prior lesson
    # to surface in the LLM prompt under recalled_memories.
    verdict = json.dumps({"multiplier": 0.7, "halt": False, "reasoning": "memory warns"})
    captured_user: dict[str, str] = {}

    def fake_call(*, system, user, max_tokens, temperature):
        captured_user["user"] = user
        return _llm_response(verdict)

    # min_similarity=-1.0 lets the deterministic hash provider's near-zero
    # cosine through — in production OpenAI embeddings produce semantically
    # similar vectors for "failed_breakout SPY" anchors and post-mortem
    # narratives, but the hash backend is structurally unable to do that.
    # The test pins WIRING (capture -> store -> recall -> prompt), not
    # the embedding's semantic quality.
    r = AutonomousReasoner(
        memory_store=store,
        embedding_provider=provider,
        recall_min_similarity=-1.0,
    )
    with patch("src.agents.autonomous_reasoner.call_llm", side_effect=fake_call):
        out = r.evaluate(_ctx())

    assert out.multiplier == 0.7
    payload = json.loads(captured_user["user"])
    recalled = payload["recalled_memories"]
    assert len(recalled) >= 1
    # The prior loss surfaces in the prompt — anonymized (no SPY).
    joined = " ".join(recalled)
    assert "failed_breakout" in joined
    assert "loss" in joined
    assert "SPY" not in joined  # anonymized
    assert "[ASSET_" in joined  # placeholder present


def test_existing_reasoner_tests_still_pass_smoke():
    """Smoke check that the legacy AutonomousReasoner still works with
    no memory configured. Detailed coverage lives in
    test_autonomous_reasoner.py — this is just a guard rail."""
    verdict = json.dumps({"multiplier": 0.85, "halt": False, "reasoning": "x"})
    r = AutonomousReasoner()  # default — no memory
    with patch(
        "src.agents.autonomous_reasoner.call_llm", return_value=_llm_response(verdict)
    ):
        out = r.evaluate(_ctx(symbol="QQQ", side="buy", strategy="ma_pullback"))
    assert out.multiplier == 0.85
    assert out.halt is False
