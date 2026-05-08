"""SilverAgent tests."""

from __future__ import annotations

from src.agents.base import AgentStatus, AssetClass
from src.agents.silver_agent import SilverAgent
from src.data.universe import Universe


def test_silver_agent_default_universe_non_empty():
    Universe.reload()
    a = SilverAgent()
    expected = Universe.named("silver")
    assert a.universe == expected
    assert len(a.universe) > 0
    assert "SLV" in a.universe


def test_silver_agent_default_loadout_reuses_gold_tuning():
    """v2: silver_agent reuses the gold-tuned strategies because silver
    is essentially a high-beta amplifier of gold's macro driver. We pin
    the strategy names so a future silver-specific tuning that diverges
    from gold (e.g. SIL miners need their own ATR multiplier) must
    update this test as part of the refactor."""
    a = SilverAgent()
    names = [s.name for s in a.strategies]
    assert "ma_pullback_trend_gold" in names
    assert "failed_breakout_gold" in names
    assert len(a.strategies) >= 2


def test_silver_agent_evaluate_returns_empty_with_no_strategies():
    """Caller can still inject an empty strategies list explicitly."""
    a = SilverAgent(strategies=[])
    out = a.evaluate({})
    assert out == []
    assert a._last_eval_ts is not None


def test_silver_agent_class_metadata():
    assert SilverAgent.name == "silver_agent"
    assert SilverAgent.asset_class is AssetClass.SILVER


def test_silver_agent_status_serializes():
    a = SilverAgent()
    s = a.status()
    assert isinstance(s, AgentStatus)
    assert s.name == "silver_agent"
    assert s.asset_class is AssetClass.SILVER
    d = s.to_dict()
    assert d["asset_class"] == "silver"
    assert d["name"] == "silver_agent"
