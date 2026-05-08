"""ma_pullback_trend_bonds — bond-tuned variant of ma_pullback_trend.

Same logic as the equity-default ``ma_pullback_trend``; only the parameters
change to suit bonds' slower trend cycles and lower volatility. The universe
is resolved through ``Universe.for_strategy(self.name)`` and is keyed to
``bonds`` in :file:`docs/universes.yaml` (TLT, IEF, SHY, AGG, BND, HYG, LQD).

Bonds-specific reasoning:
- ``fast_period`` 50 (vs 20): bonds trend on a multi-month cadence; the
  20 SMA is too noisy on something like TLT during a Fed-pause regime
  where dailies oscillate but the structural trend is intact.
- ``slow_period`` 200 (unchanged): the 200 SMA still works as the broad
  risk-on/risk-off filter — long-duration bonds above their 200 SMA is
  the textbook "rates-down, duration-up" regime.
- ``slope_lookback`` 10 (vs 5): require the slope to be persistently
  rising over a half-month, not just five days, because bond moves are
  slower and a 5-day slope can flip on a single FOMC headline.
- ``pullback_atr_mult`` 0.4 (vs 0.5): bond ATR is a tighter % of price
  than equities, so the pullback trigger needs to be slightly more
  generous as a fraction of ATR to actually fire.
- ``atr_stop_mult`` 1.5 (vs 2.0): low-vol asset; tighter stop limits
  loss size while still surviving an FOMC overnight.
- ``target_r`` 3.0 (unchanged): still the engine-default 3R bracket;
  bonds offer plenty of room to a 3R target on the multi-month moves.

Note: the parent class is imported via the module (not ``from ... import``)
so that ``load_strategy`` (which iterates module attributes and instantiates
the first Strategy subclass) picks up :class:`MaPullbackTrendBonds` rather
than the re-exported parent.
"""

from __future__ import annotations

from src.strategies import ma_pullback_trend as _mpt


class MaPullbackTrendBonds(_mpt.MaPullbackTrend):
    """Bond-tuned MA pullback. Slower, tighter, longer-confirmation."""

    name = "ma_pullback_trend_bonds"

    def __init__(self, params: _mpt.MaPullbackTrendParams | None = None) -> None:
        super().__init__(
            params
            or _mpt.MaPullbackTrendParams(
                fast_period=50,
                slow_period=200,
                slope_lookback=10,
                pullback_atr_mult=0.4,
                atr_stop_mult=1.5,
                target_r=3.0,
            )
        )
