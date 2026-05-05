"""Promotion gate tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.backtest.promotion import Decision, clone_alarm, gate


def _clean_metrics(**overrides: float) -> dict:
    """Baseline 'all gates clean' metrics dict; override fields per-test."""
    base = {
        "n_trades": 50,
        "profit_factor": 1.5,
        "max_dd": 0.10,
        "sharpe": 1.2,
        "per_window_sharpe_mean": 1.0,
        "per_window_sharpe_std": 0.2,  # std/|mean| = 0.2, well below 0.5
        "pf_concentration": 0.10,
    }
    base.update(overrides)
    return base


def _returns(seed: int, n: int = 200, mu: float = 0.001, sigma: float = 0.01) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n)
    return pd.Series(rng.normal(mu, sigma, n), index=idx)


# ---------------------------------------------------------------------------
# gate() decision tests
# ---------------------------------------------------------------------------


def test_n_trades_below_threshold_rejects():
    metrics = _clean_metrics(n_trades=29)
    res = gate(metrics)
    assert res.decision == Decision.REJECT
    assert any("n_trades" in r and "29" in r for r in res.reasons)


def test_profit_factor_just_below_threshold():
    """profit_factor=1.19 — above 1.0 (no hard fail), below 1.20 (soft fail) → MARGINAL."""
    metrics = _clean_metrics(profit_factor=1.19)
    res = gate(metrics)
    assert res.decision in (Decision.REJECT, Decision.MARGINAL)
    assert res.decision == Decision.MARGINAL
    assert any("profit_factor" in r for r in res.reasons)


def test_profit_factor_just_above_threshold_approves():
    """profit_factor=1.21, all else clean → APPROVE."""
    metrics = _clean_metrics(profit_factor=1.21)
    res = gate(metrics)
    assert res.decision == Decision.APPROVE
    assert res.reasons == ()


def test_max_dd_just_above_threshold():
    """max_dd=0.21 — above 0.20 soft (MARGINAL), below 0.30 catastrophic (no REJECT)."""
    metrics = _clean_metrics(max_dd=0.21)
    res = gate(metrics)
    assert res.decision in (Decision.REJECT, Decision.MARGINAL)
    assert res.decision == Decision.MARGINAL
    assert any("max_dd" in r for r in res.reasons)


def test_max_dd_catastrophic_rejects():
    metrics = _clean_metrics(max_dd=0.35)
    res = gate(metrics)
    assert res.decision == Decision.REJECT


def test_correlation_promotion_threshold_marginal():
    """rho ~ 0.51 with a live strategy → MARGINAL (preferred-fail tier)."""
    candidate = _returns(seed=1)
    # Build a series correlated ~0.51 with candidate.
    rng = np.random.default_rng(2)
    noise = pd.Series(rng.normal(0, 0.01, len(candidate)), index=candidate.index)
    live = 0.6 * candidate + 0.9 * noise  # mix tuned to land ~0.5 corr
    rho = candidate.corr(live)
    assert 0.40 < abs(rho) < 0.65, f"test fixture drift: rho={rho}"

    metrics = _clean_metrics()
    res = gate(metrics, baseline_returns=candidate, live_strategies={"live_a": live})
    assert res.decision == Decision.MARGINAL
    assert any("correlation" in r for r in res.reasons)


def test_correlation_alarm_threshold_rejects():
    """rho > 0.70 with a live strategy → REJECT (clone)."""
    candidate = _returns(seed=3)
    # Near-clone: tiny noise → very high correlation.
    rng = np.random.default_rng(4)
    noise = pd.Series(rng.normal(0, 0.001, len(candidate)), index=candidate.index)
    live = candidate + noise
    rho = candidate.corr(live)
    assert abs(rho) > 0.71, f"test fixture drift: rho={rho}"

    metrics = _clean_metrics()
    res = gate(metrics, baseline_returns=candidate, live_strategies={"live_a": live})
    assert res.decision == Decision.REJECT
    assert any("correlation" in r and "alarm" in r for r in res.reasons)


def test_per_window_stability_marginal():
    """per_window_sharpe_std/|mean| = 1.5 → MARGINAL."""
    metrics = _clean_metrics(per_window_sharpe_mean=0.4, per_window_sharpe_std=0.6)
    # 0.6 / 0.4 = 1.5
    res = gate(metrics)
    assert res.decision == Decision.MARGINAL
    assert any("per_window" in r for r in res.reasons)


def test_all_clean_approves():
    metrics = _clean_metrics()
    res = gate(metrics)
    assert res.decision == Decision.APPROVE
    assert res.reasons == ()
    assert res.metrics_snapshot["sharpe"] == 1.2
    assert res.metrics_snapshot["n_trades"] == 50.0


def test_sharpe_below_threshold_marginal():
    metrics = _clean_metrics(sharpe=0.3)
    res = gate(metrics)
    assert res.decision == Decision.MARGINAL
    assert any("sharpe" in r for r in res.reasons)


def test_pf_concentration_above_threshold_marginal():
    metrics = _clean_metrics(pf_concentration=0.35)
    res = gate(metrics)
    assert res.decision == Decision.MARGINAL
    assert any("pf_concentration" in r for r in res.reasons)


def test_missing_pf_concentration_warns_but_can_approve():
    """If concentration not provided, that check is skipped — APPROVE still possible."""
    metrics = _clean_metrics()
    metrics.pop("pf_concentration")
    res = gate(metrics)
    # No concentration data, all other gates clean → APPROVE; warning suppressed.
    assert res.decision == Decision.APPROVE


def test_net_losing_rejects():
    """profit_factor < 1.0 is a hard reject regardless of other metrics."""
    metrics = _clean_metrics(profit_factor=0.85)
    res = gate(metrics)
    assert res.decision == Decision.REJECT
    assert any("net-losing" in r for r in res.reasons)


# ---------------------------------------------------------------------------
# clone_alarm tests
# ---------------------------------------------------------------------------


def test_clone_alarm_high_correlation_returns_true():
    """rho ~ 0.85 → True."""
    a = _returns(seed=10)
    rng = np.random.default_rng(11)
    # Mix to land ~0.85 correlation: a + small noise.
    b = a * 1.0 + pd.Series(rng.normal(0, 0.005, len(a)), index=a.index)
    rho = a.corr(b)
    assert 0.80 < abs(rho) < 0.95, f"test fixture drift: rho={rho}"
    assert clone_alarm("strat_a", a, b, threshold=0.7) is True


def test_clone_alarm_low_correlation_returns_false():
    """rho ~ 0.3 → False."""
    a = _returns(seed=20)
    rng = np.random.default_rng(21)
    # Heavier noise dominates → low correlation.
    b = 0.3 * a + pd.Series(rng.normal(0, 0.02, len(a)), index=a.index)
    rho = a.corr(b)
    assert abs(rho) < 0.5, f"test fixture drift: rho={rho}"
    assert clone_alarm("strat_a", a, b, threshold=0.7) is False


def test_clone_alarm_threshold_boundary():
    """clone_alarm uses absolute correlation; exact threshold is exclusive."""
    a = _returns(seed=30)
    # b == a → rho = 1.0
    assert clone_alarm("x", a, a.copy(), threshold=0.7) is True
    # b uncorrelated → rho ~ 0
    rng = np.random.default_rng(31)
    b = pd.Series(rng.normal(0, 0.01, len(a)), index=a.index)
    assert clone_alarm("x", a, b, threshold=0.7) is False
