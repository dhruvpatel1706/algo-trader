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


def test_apply_reasoner_is_noop_when_unconfigured():
    """Default-constructed agents have no reasoner — passthrough behavior."""
    from decimal import Decimal

    from src.strategies.base import Signal

    class _A(Agent):
        name = "a"

        def evaluate(self, bars):
            return []

    a = _A(strategies=[], universe=("SPY",))
    sig = Signal(
        symbol="SPY",
        side="buy",
        entry=Decimal("100"),
        stop=Decimal("95"),
        target=None,
        confidence=0.7,
        strategy_tag="x",
        timestamp=datetime.now(UTC),
    )
    out = a._apply_reasoner([sig])
    assert out == [sig]
    assert a._last_judgments == []


def test_apply_reasoner_runs_when_configured():
    """When a reasoner + context builder is provided, the agent calls them
    and adjusts confidence accordingly. Audit trail lives on `_last_judgments`."""
    from decimal import Decimal

    from src.agents.autonomous_reasoner import SignalContext, SignalJudgment
    from src.strategies.base import Signal

    class _StubReasoner:
        def evaluate(self, ctx):
            return SignalJudgment(
                multiplier=0.5,
                halt=False,
                reasoning="dampened",
                provider="stub",
                elapsed_ms=1,
                asof="2026-05-06T00:00:00+00:00",
            )

    def ctx_builder(sig):
        return SignalContext(
            symbol=sig.symbol,
            side=sig.side,
            strategy=sig.strategy_tag,
            rule_confidence=sig.confidence,
            entry_price=float(sig.entry),
            stop_price=float(sig.stop),
            target_price=None,
        )

    class _A(Agent):
        name = "a"

        def evaluate(self, bars):
            return []

    a = _A(
        strategies=[],
        universe=("SPY",),
        reasoner=_StubReasoner(),  # type: ignore[arg-type]
        reasoner_context_builder=ctx_builder,
    )
    sig = Signal(
        symbol="SPY",
        side="buy",
        entry=Decimal("100"),
        stop=Decimal("95"),
        target=None,
        confidence=0.8,
        strategy_tag="x",
        timestamp=datetime.now(UTC),
    )
    out = a._apply_reasoner([sig])
    assert len(out) == 1
    assert out[0].confidence == pytest.approx(0.4)
    assert len(a._last_judgments) == 1
    assert a._last_judgments[0].multiplier == 0.5
