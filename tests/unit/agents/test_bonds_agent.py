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


def test_bonds_agent_default_loadout_includes_ma_pullback_trend_bonds():
    """v2: bonds_agent ships with the bond-tuned MA pullback strategy.

    The empty-loadout v1 was a placeholder — H2.4 wires real low-vol-tuned
    strategies so the bonds leg can actually contribute decorrelated
    signal flow to the portfolio. Pin the names so that future
    refactors that swap parameter sets must also update this test.
    """
    a = BondsAgent()
    names = [s.name for s in a.strategies]
    assert "ma_pullback_trend_bonds" in names
    assert len(a.strategies) >= 1


def test_bonds_agent_evaluate_returns_empty_with_no_strategies():
    """Caller can still inject an empty strategies list explicitly —
    e.g. integration tests that want a bonds-shaped agent without
    triggering trade logic. Default-constructed agents now wire real
    strategies (see test_bonds_agent_default_loadout)."""
    a = BondsAgent(strategies=[])
    out = a.evaluate({})
    assert out == []
    assert a._last_eval_ts is not None


def test_bonds_agent_class_metadata():
    assert BondsAgent.name == "bonds_agent"
    assert BondsAgent.asset_class is AssetClass.BONDS
