"""Property-based tests for src.backtest.promotion.

The promotion gate is the chokepoint between research backtests and any
strategy going live. Bug here = unsafe strategies promoted to paper, or
worse, eventually to live capital. Hard invariants:

- Any sample with n_trades < n_trades_min must NOT be APPROVE.
- Any sample with profit_factor < 1.0 must NOT be APPROVE (net-losing).
- Any sample with max_dd > 0.30 must NOT be APPROVE (catastrophic).
- gate() is a pure function: same inputs → same Decision. Idempotent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from src.backtest.promotion import Decision, _safe_corr, clone_alarm, gate

pytestmark = pytest.mark.property


_metrics_strategy = st.fixed_dictionaries(
    {
        "n_trades": st.integers(min_value=0, max_value=10000),
        "profit_factor": st.floats(
            min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
        "max_dd": st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        "sharpe": st.floats(
            min_value=-3.0, max_value=5.0, allow_nan=False, allow_infinity=False
        ),
    }
)


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(metrics=_metrics_strategy)
def test_gate_never_approves_below_n_trades_threshold(metrics):
    """If n_trades < 30 (default), gate() must not return APPROVE."""
    if metrics["n_trades"] >= 30:
        return  # Out of scope for this property.
    result = gate(metrics)
    assert result.decision != Decision.APPROVE, (
        f"APPROVED with n_trades={metrics['n_trades']} (default min=30)"
    )


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(metrics=_metrics_strategy)
def test_gate_never_approves_when_net_losing(metrics):
    """profit_factor < 1.0 means losses exceed wins → must REJECT."""
    if metrics["profit_factor"] >= 1.0:
        return
    result = gate(metrics)
    assert result.decision != Decision.APPROVE
    assert result.decision == Decision.REJECT


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(metrics=_metrics_strategy)
def test_gate_never_approves_above_catastrophic_dd(metrics):
    """max_dd > 0.30 must REJECT (hard fail)."""
    if metrics["max_dd"] <= 0.30:
        return
    result = gate(metrics)
    assert result.decision == Decision.REJECT


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(metrics=_metrics_strategy)
def test_gate_decision_is_deterministic(metrics):
    """Same inputs → identical GateResult. No hidden state."""
    a = gate(metrics)
    b = gate(metrics)
    assert a.decision == b.decision
    assert a.reasons == b.reasons
    assert a.metrics_snapshot == b.metrics_snapshot


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(metrics=_metrics_strategy)
def test_gate_returns_one_of_three_decisions(metrics):
    """Decision is always one of the three valid enum values."""
    result = gate(metrics)
    assert result.decision in (Decision.APPROVE, Decision.REJECT, Decision.MARGINAL)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    n_trades=st.integers(min_value=30, max_value=10000),
    profit_factor=st.floats(min_value=1.2, max_value=5.0, allow_nan=False),
    max_dd=st.floats(min_value=0.0, max_value=0.20, allow_nan=False),
    sharpe=st.floats(min_value=0.5, max_value=5.0, allow_nan=False),
    pf_concentration=st.floats(min_value=0.0, max_value=0.20, allow_nan=False),
)
def test_gate_approves_when_all_thresholds_met(
    n_trades, profit_factor, max_dd, sharpe, pf_concentration
):
    """If every default threshold is satisfied, gate must APPROVE."""
    metrics = {
        "n_trades": n_trades,
        "profit_factor": profit_factor,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "pf_concentration": pf_concentration,
    }
    result = gate(metrics)
    assert result.decision == Decision.APPROVE, (
        f"Did not APPROVE with all thresholds met: {metrics}, reasons={result.reasons}"
    )


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2**32 - 1),
    n=st.integers(min_value=2, max_value=200),
)
def test_safe_corr_in_minus_one_to_one(seed, n):
    """Correlations are mathematically bounded in [-1, 1]."""
    rng = np.random.default_rng(seed)
    a = pd.Series(rng.normal(size=n))
    b = pd.Series(rng.normal(size=n))
    rho = _safe_corr(a, b)
    assert -1.0 <= rho <= 1.0
    assert not np.isnan(rho)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_safe_corr_handles_zero_variance(seed):
    """Constant series → 0.0 (correlation undefined, must not raise)."""
    a = pd.Series([1.0] * 50)
    b = pd.Series([2.0] * 50)
    assert _safe_corr(a, b) == 0.0


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    threshold=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_clone_alarm_returns_bool(threshold, seed):
    """clone_alarm always returns a bool, never raises."""
    rng = np.random.default_rng(seed)
    a = pd.Series(rng.normal(size=80))
    b = pd.Series(rng.normal(size=80))
    out = clone_alarm("strat_a", a, b, threshold=threshold)
    assert isinstance(out, bool | np.bool_)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(metrics=_metrics_strategy)
def test_gate_snapshot_contains_required_keys(metrics):
    """metrics_snapshot must always have the canonical keys for downstream UI."""
    result = gate(metrics)
    for key in ("n_trades", "profit_factor", "max_dd", "sharpe", "max_correlation"):
        assert key in result.metrics_snapshot
        assert isinstance(result.metrics_snapshot[key], float)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    metrics=_metrics_strategy,
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_gate_high_correlation_with_live_strategy_is_hard_fail(metrics, seed):
    """If candidate is identical to a live strategy, gate must REJECT regardless of metrics."""
    rng = np.random.default_rng(seed)
    base = pd.Series(rng.normal(size=120))
    live = {"momo_v1": base.copy()}
    result = gate(metrics, baseline_returns=base, live_strategies=live)
    # Correlation will be 1.0 → above alarm threshold (0.7) → REJECT.
    assert result.decision == Decision.REJECT
