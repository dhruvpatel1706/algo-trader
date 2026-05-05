"""BondsAgent tests."""

from __future__ import annotations

from src.agents.base import AssetClass
from src.agents.bonds_agent import BondsAgent
from src.data.universe import Universe


def test_bonds_agent_default_universe_non_empty():
    Universe.reload()
    a = BondsAgent()
    expected = Universe.named("bonds")
    assert a.universe == expected
    assert len(a.universe) > 0


def test_bonds_agent_starts_with_empty_strategy_list():
    a = BondsAgent()
    assert a.strategies == []


def test_bonds_agent_evaluate_returns_empty_with_no_strategies():
    a = BondsAgent()
    out = a.evaluate({})
    assert out == []
    assert a._last_eval_ts is not None


def test_bonds_agent_class_metadata():
    assert BondsAgent.name == "bonds_agent"
    assert BondsAgent.asset_class is AssetClass.BONDS
