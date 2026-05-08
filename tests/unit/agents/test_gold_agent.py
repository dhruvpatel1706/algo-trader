"""GoldAgent tests."""

from __future__ import annotations

from src.agents.base import AssetClass
from src.agents.gold_agent import GoldAgent
from src.data.universe import Universe


def test_gold_agent_default_universe_non_empty():
    Universe.reload()
    a = GoldAgent()
    expected = Universe.named("gold")
    assert a.universe == expected
    assert len(a.universe) > 0


def test_gold_agent_default_loadout_pairs_trend_and_failed_breakdown():
    """v2: gold_agent ships with both trend (ma_pullback) and reversal
    (failed_breakout) strategies tuned for gold's macro-shock profile.
    Pinning the names ensures future refactors that swap parameter
    sets must also update this test."""
    a = GoldAgent()
    names = [s.name for s in a.strategies]
    assert "ma_pullback_trend_gold" in names
    assert "failed_breakout_gold" in names
    assert len(a.strategies) >= 2


def test_gold_agent_evaluate_returns_empty_with_no_strategies():
    """Caller can still inject an empty strategies list explicitly."""
    a = GoldAgent(strategies=[])
    out = a.evaluate({})
    assert out == []
    assert a._last_eval_ts is not None


def test_gold_agent_class_metadata():
    assert GoldAgent.name == "gold_agent"
    assert GoldAgent.asset_class is AssetClass.GOLD
