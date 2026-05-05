"""EquityAgent tests."""

from __future__ import annotations

from src.agents.base import AssetClass
from src.agents.equity_agent import EquityAgent
from src.data.universe import Universe
from src.strategies.failed_breakout import FailedBreakout
from src.strategies.ma_pullback_trend import MaPullbackTrend
from src.strategies.mr_etf import MrEtf


def test_equity_agent_loads_default_strategies():
    Universe.reload()
    a = EquityAgent()
    names = {type(s).__name__ for s in a.strategies}
    assert names == {"MrEtf", "MaPullbackTrend", "FailedBreakout"}
    # individual instances
    assert any(isinstance(s, MrEtf) for s in a.strategies)
    assert any(isinstance(s, MaPullbackTrend) for s in a.strategies)
    assert any(isinstance(s, FailedBreakout) for s in a.strategies)


def test_equity_agent_default_universe_is_liquid_etfs():
    Universe.reload()
    a = EquityAgent()
    expected = Universe.named("liquid_etfs_top20")
    assert a.universe == expected
    assert len(a.universe) > 0


def test_equity_agent_class_metadata():
    assert EquityAgent.name == "equity_agent"
    assert EquityAgent.asset_class is AssetClass.EQUITY


def test_equity_agent_evaluate_with_empty_bars_returns_empty_list():
    Universe.reload()
    a = EquityAgent()
    out = a.evaluate({})
    assert out == []
    # last_eval_ts gets stamped even on empty input
    assert a._last_eval_ts is not None


def test_equity_agent_accepts_custom_strategies_and_universe():
    Universe.reload()
    a = EquityAgent(strategies=[], universe=("SPY",), heat_allocation=0.4)
    assert a.strategies == []
    assert a.universe == ("SPY",)
    assert a.heat_allocation == 0.4
