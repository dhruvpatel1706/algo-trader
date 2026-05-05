"""Agent ABC + AgentStatus DTO tests."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from src.agents.base import Agent, AgentStatus, AssetClass


def test_agent_abc_cannot_instantiate():
    """Agent is abstract — direct instantiation must raise."""
    with pytest.raises(TypeError):
        Agent(strategies=[], universe=(), heat_allocation=0.0)  # type: ignore[abstract]


def test_agent_subclass_initializes_state_defaults():
    """Subclasses get sane initial state without live data."""

    class _StubAgent(Agent):
        name = "stub"
        asset_class = AssetClass.EQUITY

        def evaluate(self, bars):
            return []

    a = _StubAgent(strategies=[], universe=("SPY",), heat_allocation=0.25)
    assert a.universe == ("SPY",)
    assert a.heat_allocation == 0.25
    assert a._state == "paper"
    assert math.isnan(a._coherence)
    assert a._last_eval_ts is None
    assert a._n_open_positions == 0


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0, -1.0])
def test_agent_rejects_out_of_range_heat_allocation(bad):
    class _StubAgent(Agent):
        name = "stub"
        asset_class = AssetClass.EQUITY

        def evaluate(self, bars):
            return []

    with pytest.raises(ValueError):
        _StubAgent(strategies=[], universe=("SPY",), heat_allocation=bad)


def test_agent_status_to_dict_is_json_safe():
    status = AgentStatus(
        name="stub",
        asset_class=AssetClass.EQUITY,
        state="paper",
        heat_allocation=0.5,
        coherence=float("nan"),
        n_open_positions=0,
        last_eval_ts=datetime(2026, 1, 1, tzinfo=UTC),
        notes="hello",
    )
    d = status.to_dict()
    assert d["name"] == "stub"
    # Enum -> str
    assert d["asset_class"] == "equity"
    assert isinstance(d["asset_class"], str)
    # datetime -> isoformat
    assert d["last_eval_ts"] == "2026-01-01T00:00:00+00:00"
    assert d["notes"] == "hello"


def test_agent_status_to_dict_handles_none_timestamp():
    status = AgentStatus(
        name="stub",
        asset_class=AssetClass.GOLD,
        state="paper",
        heat_allocation=0.0,
        coherence=float("nan"),
        n_open_positions=0,
        last_eval_ts=None,
    )
    d = status.to_dict()
    assert d["last_eval_ts"] is None


def test_agent_status_method_returns_snapshot():
    class _StubAgent(Agent):
        name = "stub"
        asset_class = AssetClass.BONDS

        def evaluate(self, bars):
            return []

    a = _StubAgent(strategies=[], universe=("TLT",), heat_allocation=0.1)
    s = a.status()
    assert isinstance(s, AgentStatus)
    assert s.name == "stub"
    assert s.asset_class is AssetClass.BONDS
    assert s.state == "paper"
    assert math.isnan(s.coherence)


def test_assetclass_values_stable():
    """The string values are part of the wire format — pin them."""
    assert AssetClass.EQUITY.value == "equity"
    assert AssetClass.GOLD.value == "gold"
    assert AssetClass.BONDS.value == "bonds"
    assert AssetClass.CRYPTO.value == "crypto"
    assert AssetClass.GOVERNANCE.value == "governance"
