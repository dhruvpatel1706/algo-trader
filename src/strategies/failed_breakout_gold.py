"""failed_breakout_gold — gold-tuned variant of failed_breakout.

Same logic as the equity-default ``failed_breakout``; only the parameters
change to suit gold's characteristic shake-out + reversal pattern. The
universe is resolved through ``Universe.for_strategy(self.name)`` and is
keyed to ``gold`` in :file:`docs/universes.yaml` (GLD, IAU, GDX).

Why a gold-specific variant exists:
- Gold's failed-breakdown setups are common around macro headlines: a
  DXY rip drives gold below the prior range, then mean-reverts as
  positioning stabilises. The default equity params under-weight the
  ATR cushion needed; gold's typical 1.5-2.5% intraday range eats
  through the equity-default 1.0 ATR stop.
- ``channel_period`` 20 (unchanged): the textbook 20-day Donchian
  range works well on gold's swing-trade cadence.
- ``adx_min`` 18 (vs 20): gold's regimes are noisier than US large
  caps; a slightly looser tape filter lets in genuine setups that the
  20 cutoff would reject.
- ``wvf_min_quantile`` 0.85 (vs 0.80): require a deeper exhaustion
  before fading the break — gold has frequent shallow bear gaps that
  do not deserve a fade.
- ``atr_stop_mult`` 1.8 (vs 1.0): wider stop because gold's intraday
  whipsaw can easily blow through a 1-ATR stop placed below the wick.
- ``min_reward_r`` 1.5 (vs 1.2): higher cushion on the RR so a partial
  reversion still pays.
"""

from __future__ import annotations

from src.strategies import failed_breakout as _fb


class FailedBreakoutGold(_fb.FailedBreakout):
    """Gold-tuned failed-breakdown fade."""

    name = "failed_breakout_gold"

    def __init__(self, params: _fb.FailedBreakoutParams | None = None) -> None:
        super().__init__(
            params
            or _fb.FailedBreakoutParams(
                channel_period=20,
                adx_min=18.0,
                wvf_min_quantile=0.85,
                atr_stop_mult=1.8,
                min_reward_r=1.5,
            )
        )
