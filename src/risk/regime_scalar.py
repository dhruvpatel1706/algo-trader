"""Regime-conditional exposure scalar.

Maps the current SPY-derived macro regime onto a per-strategy size multiplier.
This is the single largest lever for compressing per-window Sharpe std/mean —
every strategy on the 2026-05-07 leaderboard fails the stability gate
(`per_window_std/|mean| ≤ 0.50`) at 2.7–9.5×. The strategies work in their
native regime and bleed in others; suppressing exposure when the regime is
hostile is the structural fix.

Design:
  - Mean-reversion strategies (`mr_etf`, `range_shift_pullback`) thrive in
    *transition* regimes (chop) and bleed in clean trends. So `risk_on +
    rising-VIX` is unfavorable; `risk_off` (already a chop/correction
    signal) is fine.
  - Trend strategies (`ma_pullback_trend`, `momentum_xs`) thrive in
    *trending* regimes (risk_on with rising 200 SMA) and lose in
    sideways markets. `transition` cuts size; `risk_off` cuts harder.
  - `failed_breakout` is regime-neutral but slightly less reliable in
    strong trends — modest dampening when momentum is overheated.

The scalar is composed multiplicatively with the LLM reasoner's multiplier
in :class:`src.runtime.trade_pipeline.TradePipeline._process_signal`.
A scalar below 1.0 dampens; below 0.5 effectively gates. We never UPSIZE
above 1.0 here — sizing is always anchored to the rule-based confidence.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from src.strategies.macro_regime_filter import (
    RegimeClassification,
    classify_regime,
)


# Strategy classes by behavior. New strategies should be added to whichever
# bucket matches their dominant edge; missing strategies default to 1.0
# (no regime scaling) so a typo never accidentally zeros a working strategy.
_MEAN_REVERSION = frozenset({"mr_etf", "range_shift_pullback"})
_TREND_FOLLOWING = frozenset({"ma_pullback_trend", "momentum_xs"})
_BREAKOUT = frozenset({"failed_breakout"})


def regime_scalar_for_strategy(
    strategy_tag: str,
    regime: RegimeClassification,
) -> float:
    """Return the exposure multiplier for ``strategy_tag`` under ``regime``.

    Output is in [0.0, 1.0]; we never lift exposure above the rule-based
    sizing decision.
    """
    label = regime.label
    if strategy_tag in _MEAN_REVERSION:
        # Mean-reversion is happiest in chop. Strong risk_on (clean trend +
        # low vol) chews it up; risk_off has its own mean-reversion edge
        # but elevated tail risk so we still trim a bit.
        if label == "risk_on":
            return 0.3
        if label == "risk_off":
            return 0.7
        return 1.0  # transition — native habitat
    if strategy_tag in _TREND_FOLLOWING:
        # Trend-following needs a real trend. Transition is sideways =
        # whipsaw; risk_off can be a one-way trend down so partial size.
        if label == "transition":
            return 0.5
        if label == "risk_off":
            return 0.7
        return 1.0  # risk_on — native habitat
    if strategy_tag in _BREAKOUT:
        # Failed-breakout fades work best when there's a level to fail
        # against. Both pure trend (risk_on) and pure capitulation
        # (risk_off + high VIX) reduce setup quality slightly.
        if label == "risk_on" and regime.vix_proxy < 15.0:
            return 0.7
        if label == "risk_off" and regime.vix_proxy > 35.0:
            return 0.7
        return 1.0
    # Unknown strategy — no scaling. Safer than guessing.
    return 1.0


def regime_scalar_for_bars(
    strategy_tag: str,
    bars: dict[str, pd.DataFrame] | None,
    *,
    asset_class: Literal["equity", "gold", "silver", "bonds", "crypto", "governance"]
    = "equity",
) -> tuple[float, RegimeClassification | None]:
    """Compute the regime classification + scalar from raw bars.

    Returns ``(1.0, None)`` when the regime can't be computed (no SPY in
    bars, asset_class is crypto where the SPY-proxy doesn't apply, etc.).
    The pipeline can then skip the dampened-refusal log path.
    """
    if not bars:
        return 1.0, None
    # Crypto runs 24/7 against a different regime structure (BTC dominance,
    # funding rates, on-chain flows) — the SPY-proxy classification doesn't
    # transfer. Skip rather than apply a misleading equity regime label.
    if asset_class == "crypto":
        return 1.0, None
    if "SPY" not in bars:
        return 1.0, None

    regime = classify_regime(bars)
    return regime_scalar_for_strategy(strategy_tag, regime), regime
