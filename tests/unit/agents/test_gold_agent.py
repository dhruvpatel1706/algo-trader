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


def test_gold_agent_starts_with_empty_strategy_list():
    a = GoldAgent()
    assert a.strategies == []


def test_gold_agent_evaluate_returns_empty_with_no_strategies():
    a = GoldAgent()
    out = a.evaluate({})
    assert out == []
    assert a._last_eval_ts is not None


def test_gold_agent_class_metadata():
    assert GoldAgent.name == "gold_agent"
    assert GoldAgent.asset_class is AssetClass.GOLD
