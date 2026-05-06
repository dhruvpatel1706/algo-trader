"""Dealer gamma exposure (GEX) — a regime indicator.

What it is
----------
GEX (gamma exposure) approximates how dealers are positioned in options
relative to spot. Sign convention used here matches the SqueezeMetrics
academic paper ("Gamma Exposure" — N. Squeezemetrics, 2017) and the
Goldman / JPM equity-derivatives-research desk usage:

    GEX[contract] = open_interest * contract_multiplier * gamma * spot²
    GEX[call]  =  +GEX                    (dealers short calls -> short gamma)
    GEX[put]   =  -GEX                    (dealers long puts  -> short gamma)

…wait — that's the conventional retail framing, but it's the OPPOSITE of
what the dealer book actually looks like for an index ETF like SPY where
retail is a net buyer of puts and a net seller of calls. To stay consistent
with the most-cited literature (and most commercial GEX dashboards), we
follow the SqueezeMetrics convention:

    GEX_call  = +OI * mult * gamma * S²
    GEX_put   = -OI * mult * gamma * S²
    GEX_total = sum(GEX_call) + sum(GEX_put)

Interpretation:
    GEX_total > 0:  dealers are long gamma -> they buy dips, sell rallies
                    -> mean-reverting regime, range-bound day expected.
    GEX_total < 0:  dealers are short gamma -> they sell dips, chase rallies
                    -> trend-following regime, vol expansion expected.
    |GEX_total|     scales the conviction of the regime label.

Why we ship it
--------------
This module is **scaffold + math only** — it does NOT fetch options chains.
The chain comes from `src/data/loader.py` once `POLYGON_OPTIONS_KEY` is set;
the moonshot/governance side wires GEX into ``macro_regime_filter`` to
dampen mean-reversion strategies in negative-GEX regimes.

Until then, this module is testable end-to-end against a synthetic chain
(see ``tests/unit/signals/test_gex.py``). The Black-Scholes gamma
calculation is the standard log-normal closed form; it doesn't depend on
any vendor.

The only thing missing for production is the chain feed — every other
piece (signing convention, regime classifier, NaN handling, empty-chain
fallback) is here and tested.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

# Standard equity-options contract multiplier. Index options use the same
# convention; the function takes it as a parameter so an unusual chain
# (mini-options at multiplier=10, futures options at varying multipliers)
# can be handled without forking this module.
DEFAULT_CONTRACT_MULTIPLIER: int = 100

# Regime band boundaries (in dollars of GEX per dollar of spot). These are
# loose guidelines drawn from public SqueezeMetrics research; the operator
# should tune them against backtests once a real chain feed is wired.
GEX_BAND_NEUTRAL_HIGH: float = 250_000_000.0   # >= this is positive-gamma regime
GEX_BAND_NEUTRAL_LOW: float = -250_000_000.0   # <= this is negative-gamma regime


RegimeLabel = Literal["positive_gamma", "neutral", "negative_gamma"]


@dataclass(frozen=True, slots=True)
class OptionRow:
    """A single option contract slice from a chain.

    Only the fields needed for GEX are required. ``iv`` is the Black-Scholes
    implied vol expressed as an annual decimal (0.20 = 20%). ``T`` is years
    to expiration; we leave the day-count to the caller (most chains report
    days, divide by 365).
    """

    side: Literal["call", "put"]
    strike: float
    iv: float            # implied vol (annualized, decimal)
    T: float             # years to expiration
    open_interest: int   # number of contracts open in the book


@dataclass(frozen=True, slots=True)
class GexSummary:
    """The output of `compute_dealer_gex`."""

    spot: float
    gex_total: float
    gex_calls: float
    gex_puts: float
    n_rows: int             # number of usable rows that contributed
    n_skipped: int          # rows skipped due to NaN/zero/expired
    regime: RegimeLabel


def _norm_pdf(x: float) -> float:
    """Standard normal PDF, no scipy dependency."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_gamma(
    spot: float, strike: float, iv: float, T: float, r: float = 0.0
) -> float:
    """Closed-form Black-Scholes gamma for a European option.

    gamma = phi(d1) / (S * sigma * sqrt(T))

    Returns 0 for degenerate inputs (non-positive spot/strike/T/iv) so
    callers can pass partial chains without filtering first. Risk-free
    rate ``r`` defaults to zero — for retail-scale GEX over a 1-year
    horizon the rate term is a second-order effect and the popular
    public dashboards omit it.
    """
    if spot <= 0 or strike <= 0 or iv <= 0 or T <= 0:
        return 0.0
    sigma_root_t = iv * math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / sigma_root_t
    return _norm_pdf(d1) / (spot * sigma_root_t)


def classify_regime(gex_total: float) -> RegimeLabel:
    """Bucket the total GEX into one of three regime labels."""
    if not math.isfinite(gex_total):
        return "neutral"
    if gex_total >= GEX_BAND_NEUTRAL_HIGH:
        return "positive_gamma"
    if gex_total <= GEX_BAND_NEUTRAL_LOW:
        return "negative_gamma"
    return "neutral"


def compute_dealer_gex(
    spot: float,
    chain: list[OptionRow],
    *,
    contract_multiplier: int = DEFAULT_CONTRACT_MULTIPLIER,
) -> GexSummary:
    """Aggregate GEX across an option chain.

    Empty / fully-degenerate chains return zeros + ``regime='neutral'``.
    This is the same fail-open behavior every other plug-in in the bot
    uses — a missing feed must never crash a strategy evaluation.
    """
    if spot <= 0 or not chain:
        return GexSummary(
            spot=max(spot, 0.0),
            gex_total=0.0,
            gex_calls=0.0,
            gex_puts=0.0,
            n_rows=0,
            n_skipped=len(chain),
            regime="neutral",
        )

    gex_calls = 0.0
    gex_puts = 0.0
    n_rows = 0
    n_skipped = 0
    s_squared = spot * spot

    for row in chain:
        if row.open_interest <= 0:
            n_skipped += 1
            continue
        gamma = black_scholes_gamma(spot, row.strike, row.iv, row.T)
        if gamma == 0.0 or not math.isfinite(gamma):
            n_skipped += 1
            continue
        contribution = row.open_interest * contract_multiplier * gamma * s_squared
        if row.side == "call":
            gex_calls += contribution
        else:
            gex_puts -= contribution  # SqueezeMetrics convention: puts subtract
        n_rows += 1

    gex_total = gex_calls + gex_puts
    return GexSummary(
        spot=spot,
        gex_total=gex_total,
        gex_calls=gex_calls,
        gex_puts=gex_puts,
        n_rows=n_rows,
        n_skipped=n_skipped,
        regime=classify_regime(gex_total),
    )


def gex_regime_multiplier(regime: RegimeLabel) -> float:
    """Confidence multiplier for mean-reversion strategies based on GEX regime.

    - Positive gamma -> dealers stabilize price -> mean-reversion edge is
      stronger -> multiplier > 1 (cap at 1.2 to avoid pyramiding).
    - Negative gamma -> dealers chase moves -> mean-reversion gets run over
      -> multiplier < 1 (floor at 0.5 — never veto outright; let the
      strategy's own stop handle the rest).
    - Neutral -> no information, multiplier = 1.0.

    Strategies opt in by multiplying ``signal.confidence *= gex_regime_multiplier(...)``
    in their post-filter chain. Default off to keep behavior change opt-in.
    """
    if regime == "positive_gamma":
        return 1.15
    if regime == "negative_gamma":
        return 0.65
    return 1.0


__all__ = [
    "DEFAULT_CONTRACT_MULTIPLIER",
    "GexSummary",
    "OptionRow",
    "RegimeLabel",
    "black_scholes_gamma",
    "classify_regime",
    "compute_dealer_gex",
    "gex_regime_multiplier",
]
