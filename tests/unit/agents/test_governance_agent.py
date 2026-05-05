"""GovernanceAgent tests."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from src.agents.base import AssetClass
from src.agents.governance_agent import (
    GovernanceAgent,
    GovernanceRecommendation,
)


def _status(
    name: str = "mr_etf",
    state: str = "paper",
    coherence: float = float("nan"),
):
    return SimpleNamespace(
        name=name,
        asset_class=AssetClass.EQUITY,
        state=state,
        heat_allocation=0.1,
        coherence=coherence,
        n_open_positions=0,
        last_eval_ts=datetime.now(UTC),
        notes="",
    )


def test_governance_agent_class_metadata():
    assert GovernanceAgent.name == "governance_agent"
    assert GovernanceAgent.asset_class is AssetClass.GOVERNANCE


def test_governance_agent_evaluate_with_no_state_returns_empty():
    g = GovernanceAgent()
    assert g.evaluate(None) == []
    # Bars-shaped input (the trading-agent contract) also yields nothing.
    assert g.evaluate({"SPY": object()}) == []


def test_governance_agent_recommends_kill_on_low_coherence():
    g = GovernanceAgent(coherence_kill_threshold=0.5)
    state = [_status(name="mr_etf", state="live", coherence=0.3)]
    recs = g.evaluate(state)
    assert len(recs) == 1
    rec = recs[0]
    assert isinstance(rec, GovernanceRecommendation)
    assert rec.action == "kill"
    assert rec.target_strategy == "mr_etf"
    assert "coherence" in rec.reason.lower()
    assert 0.0 <= rec.confidence <= 1.0


def test_governance_agent_recommends_investigate_when_halted():
    g = GovernanceAgent()
    state = [_status(name="ma_pullback_trend", state="halted")]
    recs = g.evaluate(state)
    assert len(recs) == 1
    assert recs[0].action == "investigate"


def test_governance_agent_skips_nan_coherence():
    """No live data yet — governance shouldn't manufacture a recommendation."""
    g = GovernanceAgent()
    state = [_status(name="mr_etf", state="paper", coherence=float("nan"))]
    assert g.evaluate(state) == []


def test_governance_agent_skips_healthy_coherence():
    g = GovernanceAgent(coherence_kill_threshold=0.5)
    state = [_status(name="mr_etf", state="live", coherence=1.05)]
    assert g.evaluate(state) == []


def test_governance_agent_evaluate_handles_multiple_statuses():
    g = GovernanceAgent(coherence_kill_threshold=0.5)
    state = [
        _status(name="mr_etf", state="live", coherence=0.3),
        _status(name="ma_pullback_trend", state="paper", coherence=float("nan")),
        _status(name="failed_breakout", state="halted"),
        _status(name="wheel_etf", state="live", coherence=0.95),
    ]
    recs = g.evaluate(state)
    actions_by_target = {r.target_strategy: r.action for r in recs}
    assert actions_by_target == {"mr_etf": "kill", "failed_breakout": "investigate"}


def test_governance_recommendation_rejects_bad_confidence():
    with pytest.raises(ValueError):
        GovernanceRecommendation(
            target_strategy="mr_etf",
            action="kill",
            reason="x",
            confidence=1.5,
            ts=datetime.now(UTC),
        )


def test_governance_recommendation_records_timestamp():
    rec = GovernanceRecommendation(
        target_strategy="mr_etf",
        action="kill",
        reason="coherence below threshold",
        confidence=0.7,
        ts=datetime.now(UTC),
    )
    assert rec.ts.tzinfo is not None
    # Frozen dataclass — confidence is set, no NaN slipped through.
    assert not math.isnan(rec.confidence)
