"""ma_pullback_trend_gold — gold-tuned variant of ma_pullback_trend.

Same logic as the equity-default ``ma_pullback_trend``; only the parameters
change to suit gold's characteristic behaviour. The universe is resolved
through ``Universe.for_strategy(self.name)`` and is keyed to ``gold`` in
:file:`docs/universes.yaml` (GLD, IAU, GDX).

Gold-specific reasoning:
- Gold trends are SLOW but VOLATILE around macro shocks — DXY spikes,
  Fed pivots, geopolitics. The pullback parameters need to absorb a
  3-5% intraday gap without eating the stop, while still gating
  entries to genuine uptrends rather than first-shock-then-fade
  whipsaws.
- ``fast_period`` 30 (vs 20): a 30-day SMA filters the noise of
  individual GLD prints better than 20 while still being responsive.
- ``slow_period`` 200 (unchanged): macro filter — gold above its
  200 DMA is the textbook bull regime.
- ``slope_lookback`` 7 (vs 5): demand a slightly more persistent
  uptrend before entering, since the first leg up in gold is often
  driven by a single news event that fades.
- ``pullback_atr_mult`` 0.6 (vs 0.5): allow a slightly deeper pullback
  before triggering — gold dips intraday more than a typical equity.
- ``atr_stop_mult`` 2.5 (vs 2.0): wider stop to ride through a typical
  shake-out without being knocked out by an FX spike.
- ``target_r`` 2.5 (vs 3.0): gold reverses fast on profit-taking;
  realising 2.5R is more reliable than holding for 3R.
"""

from __future__ import annotations

from src.strategies import ma_pullback_trend as _mpt


class MaPullbackTrendGold(_mpt.MaPullbackTrend):
    """Gold-tuned MA pullback. Wider stops, faster targets, slower trend filter."""

    name = "ma_pullback_trend_gold"

    def __init__(self, params: _mpt.MaPullbackTrendParams | None = None) -> None:
        super().__init__(
            params
            or _mpt.MaPullbackTrendParams(
                fast_period=30,
                slow_period=200,
                slope_lookback=7,
                pullback_atr_mult=0.6,
                atr_stop_mult=2.5,
                target_r=2.5,
            )
        )
