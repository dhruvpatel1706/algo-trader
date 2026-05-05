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


def test_silver_agent_starts_with_empty_strategy_list():
    a = SilverAgent()
    assert a.strategies == []


def test_silver_agent_evaluate_returns_empty_with_no_strategies():
    a = SilverAgent()
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
