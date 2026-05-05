"""ma_pullback_trend_crypto - crypto-tuned variant of ma_pullback_trend.

Same logic as the equity-default `ma_pullback_trend`; only the parameters
change to suit crypto's faster trends, shorter regime cycles, and elevated
volatility. The universe is resolved through `Universe.for_strategy(self.name)`
and is keyed to `crypto_majors` in `docs/universes.yaml`.

Note: the parent class is imported via the module (not `from ... import`) so
that `load_strategy` (which iterates module attributes and instantiates the
first Strategy subclass) picks up `MaPullbackTrendCrypto` rather than the
re-exported parent.
"""

from __future__ import annotations

from src.strategies import ma_pullback_trend as _mpt


class MaPullbackTrendCrypto(_mpt.MaPullbackTrend):
    """Crypto-tuned MA pullback.

    Parameter rationale:
    - fast_period 10 (vs 20): crypto trends shift faster
    - slow_period 50 (vs 200): the 200-day filter is too lagging for crypto
    - slope_lookback 3 (vs 5): faster trend rotation
    - pullback_atr_mult 0.7 (vs 0.5): crypto volatility makes 0.5 ATR a
      hairline trigger
    - atr_stop_mult 2.5 (vs 2.0): wider stop to ride bigger swings
    - target_r 2.5 (vs 3.0): crypto reversals come faster, take profits earlier
    """

    name = "ma_pullback_trend_crypto"

    def __init__(self, params: _mpt.MaPullbackTrendParams | None = None) -> None:
        super().__init__(
            params
            or _mpt.MaPullbackTrendParams(
                fast_period=10,
                slow_period=50,
                slope_lookback=3,
                pullback_atr_mult=0.7,
                atr_stop_mult=2.5,
                target_r=2.5,
            )
        )
