"""Promotion gate — single chokepoint for "is this strategy promotable to live paper?".

Pure functions on metrics dicts and returns series. No I/O, no Redis, no journal —
callers (e.g. governance_agent) pull from this module and execute side effects on top.

Decision tiers:
- APPROVE: every gate passes cleanly.
- REJECT: at least one HARD failure (catastrophic dd, n_trades miss, net-losing,
          clone-level correlation with a live strategy).
- MARGINAL: close to passing — soft fails but no hard failure.

Also exports `clone_alarm` for production-time correlation checks between two
already-live strategies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Decision(Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MARGINAL = "marginal"


@dataclass(frozen=True, slots=True)
class GateResult:
    decision: Decision
    reasons: tuple[str, ...]
    metrics_snapshot: dict[str, float]


def _safe_corr(a: pd.Series, b: pd.Series) -> float:
    """Pearson correlation on the overlapping index. Returns 0.0 if undefined."""
    if a is None or b is None or len(a) == 0 or len(b) == 0:
        return 0.0
    aligned = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return 0.0
    x = aligned.iloc[:, 0]
    y = aligned.iloc[:, 1]
    if x.std(ddof=1) == 0 or y.std(ddof=1) == 0:
        return 0.0
    rho = float(x.corr(y))
    if np.isnan(rho):
        return 0.0
    return rho


def _max_correlation(
    candidate: pd.Series | None,
    live_strategies: dict[str, pd.Series] | None,
) -> tuple[float, str | None]:
    """Return (max_abs_correlation, tag_of_max). (0.0, None) if no live strategies."""
    if candidate is None or not live_strategies:
        return 0.0, None
    best_rho = 0.0
    best_tag: str | None = None
    for tag, live_returns in live_strategies.items():
        rho = _safe_corr(candidate, live_returns)
        if abs(rho) > abs(best_rho):
            best_rho = rho
            best_tag = tag
    return best_rho, best_tag


def gate(  # noqa: PLR0912, PLR0915 — gate() is intentionally a flat list of independent checks; refactoring into smaller functions would obscure the audit trail
    metrics: dict,
    baseline_returns: pd.Series | None = None,
    live_strategies: dict[str, pd.Series] | None = None,
    n_trades_min: int = 30,
    profit_factor_min: float = 1.2,
    max_dd_max: float = 0.20,
    sharpe_min: float = 0.5,
    pf_concentration_max: float = 0.20,
    correlation_max_promotion: float = 0.5,
    correlation_max_alarm: float = 0.7,
    per_window_stability_max: float = 0.5,
) -> GateResult:
    """Decide APPROVE / REJECT / MARGINAL for a backtested strategy.

    `metrics` is the dict produced by `src.backtest.metrics.summarize()`, optionally
    enriched with `per_window_sharpe_mean` / `per_window_sharpe_std` from
    `walk_forward.run_walk_forward`, and (optionally) `pf_concentration` if the
    caller has computed the largest-trade share.
    """
    reasons: list[str] = []
    hard_fail = False

    # --- Pull metrics with defaults that fail loudly rather than silently passing.
    n_trades = int(metrics.get("n_trades", 0))
    profit_factor = float(metrics.get("profit_factor", 0.0))
    max_dd = float(metrics.get("max_dd", 1.0))
    sharpe = float(metrics.get("sharpe", 0.0))
    pw_mean = metrics.get("per_window_sharpe_mean")
    pw_std = metrics.get("per_window_sharpe_std")
    pf_concentration = metrics.get("pf_concentration")

    # --- HARD failures first. Any one of these → REJECT.
    if n_trades < n_trades_min:
        reasons.append(f"n_trades={n_trades} below threshold {n_trades_min}")
        hard_fail = True

    if profit_factor < 1.0:
        reasons.append(f"profit_factor={profit_factor:.2f} below 1.00 (net-losing)")
        hard_fail = True

    if max_dd > 0.30:
        reasons.append(f"max_dd={max_dd:.2%} above catastrophic threshold 30.00%")
        hard_fail = True

    # Correlation check vs live strategies — alarm tier is a hard kill.
    max_rho, max_tag = _max_correlation(baseline_returns, live_strategies)
    if abs(max_rho) > correlation_max_alarm:
        reasons.append(
            f"correlation={max_rho:.2f} with '{max_tag}' above alarm threshold "
            f"{correlation_max_alarm:.2f}"
        )
        hard_fail = True

    # --- Soft gates. Each failure is a reason; not by itself a REJECT.
    soft_fail = False

    if not hard_fail and profit_factor < profit_factor_min:
        reasons.append(
            f"profit_factor={profit_factor:.2f} below threshold {profit_factor_min:.2f}"
        )
        soft_fail = True

    if not hard_fail and max_dd > max_dd_max:
        reasons.append(f"max_dd={max_dd:.2%} above threshold {max_dd_max:.2%}")
        soft_fail = True

    if sharpe < sharpe_min:
        reasons.append(f"sharpe={sharpe:.2f} below threshold {sharpe_min:.2f}")
        soft_fail = True

    # Per-window stability: std/|mean| ratio. Skip if metrics not provided.
    if pw_mean is not None and pw_std is not None:
        denom = abs(float(pw_mean))
        if denom > 0:
            ratio = float(pw_std) / denom
            if ratio > per_window_stability_max:
                reasons.append(
                    f"per_window_sharpe_std/|mean|={ratio:.2f} above threshold "
                    f"{per_window_stability_max:.2f}"
                )
                soft_fail = True
        # Mean ~= 0 with non-zero std means no stable edge.
        elif float(pw_std) > 0:
            reasons.append(
                "per_window_sharpe_mean~=0 with nonzero std (no stable edge)"
            )
            soft_fail = True

    # Promotion-time correlation soft fail (alarm tier already caught above).
    if (
        not hard_fail
        and abs(max_rho) > correlation_max_promotion
        and abs(max_rho) <= correlation_max_alarm
    ):
        reasons.append(
            f"correlation={max_rho:.2f} with '{max_tag}' above promotion threshold "
            f"{correlation_max_promotion:.2f}"
        )
        soft_fail = True

    # P&L concentration: if the caller passed `pf_concentration`, enforce it. Else warn.
    if pf_concentration is not None:
        conc = float(pf_concentration)
        if conc > pf_concentration_max:
            reasons.append(
                f"pf_concentration={conc:.2%} above threshold {pf_concentration_max:.2%}"
            )
            soft_fail = True
    else:
        reasons.append("pf_concentration not provided in metrics; check skipped")

    snapshot: dict[str, float] = {
        "n_trades": float(n_trades),
        "profit_factor": float(profit_factor),
        "max_dd": float(max_dd),
        "sharpe": float(sharpe),
        "max_correlation": float(max_rho),
    }
    if pw_mean is not None:
        snapshot["per_window_sharpe_mean"] = float(pw_mean)
    if pw_std is not None:
        snapshot["per_window_sharpe_std"] = float(pw_std)
    if pf_concentration is not None:
        snapshot["pf_concentration"] = float(pf_concentration)

    if hard_fail:
        decision = Decision.REJECT
    elif soft_fail:
        decision = Decision.MARGINAL
    else:
        decision = Decision.APPROVE

    # APPROVE path: don't surface the "concentration skipped" warning as a reason.
    if decision == Decision.APPROVE:
        reasons = [r for r in reasons if "pf_concentration not provided" not in r]

    return GateResult(
        decision=decision,
        reasons=tuple(reasons),
        metrics_snapshot=snapshot,
    )


def clone_alarm(
    strategy_tag: str,
    returns_a: pd.Series,
    returns_b: pd.Series,
    threshold: float = 0.7,
) -> bool:
    """True if two live strategies have correlated returns above `threshold`.

    Used by governance to detect that two live strategies have effectively
    converged onto the same edge; the weaker one should be killed.

    `strategy_tag` is informational — the caller logs which strategy it's checking.
    """
    _ = strategy_tag  # kept for caller-side logging symmetry
    rho = _safe_corr(returns_a, returns_b)
    return abs(rho) > threshold
