"""failed_breakout_crypto - crypto-tuned variant of failed_breakout.

Same logic as the equity-default `failed_breakout`; only the parameters change
to suit crypto's faster ranges, higher volatility, and thinner liquidity. The
universe is resolved through `Universe.for_strategy(self.name)` and is keyed
to `crypto_majors` in `docs/universes.yaml`.

Note: the parent class is imported via the module (not `from ... import`) so
that `load_strategy` (which iterates module attributes and instantiates the
first Strategy subclass) picks up `FailedBreakoutCrypto` rather than the
re-exported parent.
"""

from __future__ import annotations

from src.strategies import failed_breakout as _fb


class FailedBreakoutCrypto(_fb.FailedBreakout):
    """Crypto-tuned variant of failed_breakout.

    Parameter rationale:
    - channel_period 14 (vs 20 for equities): crypto moves faster, shorter
      ranges still mean something
    - adx_min 18 (vs 20): crypto regimes are noisier; gate slightly looser
    - wvf_min_quantile 0.85 (vs 0.80): require deeper exhaustion since crypto
      has more frequent shallow false moves
    - atr_stop_mult 1.5 (vs 1.0): wider stop to absorb crypto's typical 5-10%
      intraday whipsaws
    - min_reward_r 1.5 (vs 1.2): higher RR cushion against thin liquidity
    """

    name = "failed_breakout_crypto"

    def __init__(self, params: _fb.FailedBreakoutParams | None = None) -> None:
        super().__init__(
            params
            or _fb.FailedBreakoutParams(
                channel_period=14,
                adx_min=18.0,
                wvf_min_quantile=0.85,
                atr_stop_mult=1.5,
                min_reward_r=1.5,
            )
        )
