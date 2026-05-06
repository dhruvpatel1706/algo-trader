"""Tests for src.signals.gex.

The math is covered against:
  - Hand-computed reference values for ATM gamma at known sigma/T.
  - Boundary conditions (T=0, sigma=0, spot=0, K=0, OI=0).
  - The SqueezeMetrics sign convention (calls add, puts subtract).
  - Regime classification at the band boundaries.
  - The fail-open empty-chain path.
"""

from __future__ import annotations

import math

import pytest
from src.signals.gex import (
    GEX_BAND_NEUTRAL_HIGH,
    GEX_BAND_NEUTRAL_LOW,
    GexSummary,
    OptionRow,
    black_scholes_gamma,
    classify_regime,
    compute_dealer_gex,
    gex_regime_multiplier,
)

# ---------------------------------------------------------------------------
# black_scholes_gamma
# ---------------------------------------------------------------------------


def test_atm_gamma_matches_closed_form():
    """For S=K=100, sigma=0.20, T=0.25, gamma should match the closed form
    we ship to within 1e-12. This is the canonical test option dealers use
    when sanity-checking a pricing library."""
    spot = strike = 100.0
    iv = 0.20
    T = 0.25
    g = black_scholes_gamma(spot, strike, iv, T)
    # phi(d1) / (S * sigma * sqrt(T))
    sigma_rt = iv * math.sqrt(T)
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * T) / sigma_rt
    expected = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (spot * sigma_rt)
    assert abs(g - expected) < 1e-12


def test_gamma_decreases_far_from_strike():
    """Gamma is bell-shaped in moneyness — far OTM/ITM gamma << ATM gamma."""
    iv, T = 0.20, 0.25
    atm = black_scholes_gamma(100.0, 100.0, iv, T)
    far_otm = black_scholes_gamma(100.0, 200.0, iv, T)
    far_itm = black_scholes_gamma(100.0, 50.0, iv, T)
    assert atm > far_otm
    assert atm > far_itm
    # Both wings should be quite small relative to ATM.
    assert far_otm < 0.05 * atm
    assert far_itm < 0.10 * atm


@pytest.mark.parametrize(
    ("spot", "strike", "iv", "T"),
    [
        (0.0, 100.0, 0.2, 0.25),  # zero spot
        (-50.0, 100.0, 0.2, 0.25),  # negative spot (degenerate)
        (100.0, 0.0, 0.2, 0.25),  # zero strike
        (100.0, 100.0, 0.0, 0.25),  # zero vol
        (100.0, 100.0, 0.2, 0.0),  # expired (T=0)
        (100.0, 100.0, -0.1, 0.25),  # negative vol
        (100.0, 100.0, 0.2, -0.1),  # negative T
    ],
)
def test_gamma_returns_zero_for_degenerate_inputs(spot, strike, iv, T):
    """No NaN, no exceptions — degenerate inputs cleanly return 0."""
    g = black_scholes_gamma(spot, strike, iv, T)
    assert g == 0.0


# ---------------------------------------------------------------------------
# classify_regime
# ---------------------------------------------------------------------------


def test_regime_at_high_band_is_positive_gamma():
    assert classify_regime(GEX_BAND_NEUTRAL_HIGH) == "positive_gamma"
    assert classify_regime(GEX_BAND_NEUTRAL_HIGH + 1.0) == "positive_gamma"
    assert classify_regime(GEX_BAND_NEUTRAL_HIGH - 1.0) == "neutral"


def test_regime_at_low_band_is_negative_gamma():
    assert classify_regime(GEX_BAND_NEUTRAL_LOW) == "negative_gamma"
    assert classify_regime(GEX_BAND_NEUTRAL_LOW - 1.0) == "negative_gamma"
    assert classify_regime(GEX_BAND_NEUTRAL_LOW + 1.0) == "neutral"


def test_regime_neutral_at_zero():
    assert classify_regime(0.0) == "neutral"


def test_regime_fails_closed_for_non_finite():
    """NaN and ±inf are signs of a degenerate computation. Fail closed
    (neutral) rather than guess at intent — the strategy multiplier will
    stay at 1.0 (identity) and the rule-based pipeline runs unmodified."""
    assert classify_regime(float("nan")) == "neutral"
    assert classify_regime(float("inf")) == "neutral"
    assert classify_regime(-float("inf")) == "neutral"


# ---------------------------------------------------------------------------
# compute_dealer_gex
# ---------------------------------------------------------------------------


def test_empty_chain_returns_zero_neutral():
    out = compute_dealer_gex(spot=100.0, chain=[])
    assert out.gex_total == 0.0
    assert out.regime == "neutral"
    assert out.n_rows == 0
    assert out.n_skipped == 0


def test_zero_spot_returns_neutral_summary():
    chain = [OptionRow(side="call", strike=100, iv=0.2, T=0.25, open_interest=1000)]
    out = compute_dealer_gex(spot=0.0, chain=chain)
    assert out.gex_total == 0.0
    assert out.n_rows == 0
    assert out.n_skipped == 1
    assert out.regime == "neutral"


def test_calls_contribute_positive_puts_negative():
    """SqueezeMetrics convention: GEX_total = +calls - puts."""
    spot = 100.0
    common = {"strike": 100.0, "iv": 0.20, "T": 0.25, "open_interest": 1_000_000}
    only_calls = compute_dealer_gex(
        spot, [OptionRow(side="call", **common)]  # type: ignore[arg-type]
    )
    only_puts = compute_dealer_gex(
        spot, [OptionRow(side="put", **common)]  # type: ignore[arg-type]
    )
    assert only_calls.gex_total > 0
    assert only_puts.gex_total < 0
    # Same |contribution|, opposite sign.
    assert abs(only_calls.gex_total + only_puts.gex_total) < 1e-6


def test_balanced_book_nets_to_zero():
    """Identical OI on call and put at the same strike => GEX cancels."""
    spot = 100.0
    common = {"strike": 100.0, "iv": 0.20, "T": 0.25, "open_interest": 500_000}
    chain = [
        OptionRow(side="call", **common),  # type: ignore[arg-type]
        OptionRow(side="put", **common),  # type: ignore[arg-type]
    ]
    out = compute_dealer_gex(spot, chain)
    assert abs(out.gex_total) < 1e-6
    assert out.n_rows == 2
    assert out.regime == "neutral"


def test_skips_zero_oi_rows():
    chain = [
        OptionRow(side="call", strike=100, iv=0.2, T=0.25, open_interest=0),
        OptionRow(side="call", strike=100, iv=0.2, T=0.25, open_interest=100),
    ]
    out = compute_dealer_gex(spot=100.0, chain=chain)
    assert out.n_rows == 1
    assert out.n_skipped == 1


def test_skips_rows_with_zero_gamma():
    """Expired (T<=0) or zero-vol contracts contribute zero gamma — skip."""
    chain = [
        OptionRow(side="call", strike=100, iv=0.0, T=0.25, open_interest=100),
        OptionRow(side="call", strike=100, iv=0.2, T=0.0, open_interest=100),
        OptionRow(side="call", strike=100, iv=0.2, T=0.25, open_interest=100),
    ]
    out = compute_dealer_gex(spot=100.0, chain=chain)
    assert out.n_rows == 1
    assert out.n_skipped == 2


def test_large_short_gamma_book_classified_as_negative_gamma():
    """Synthetic SPX-shaped book heavy in puts — should land in
    negative-gamma regime."""
    spot = 5_000.0
    chain = [
        OptionRow(side="put", strike=4_900, iv=0.15, T=30 / 365, open_interest=200_000),
        OptionRow(side="put", strike=4_950, iv=0.15, T=30 / 365, open_interest=300_000),
        OptionRow(side="put", strike=5_000, iv=0.15, T=30 / 365, open_interest=400_000),
        OptionRow(side="call", strike=5_050, iv=0.15, T=30 / 365, open_interest=100_000),
    ]
    out = compute_dealer_gex(spot, chain)
    assert out.gex_total < 0
    assert out.regime == "negative_gamma"


def test_summary_shape_is_pinned():
    """Downstream consumers depend on the field names. Pin them."""
    out = compute_dealer_gex(100.0, [])
    assert isinstance(out, GexSummary)
    assert {f for f in out.__slots__} == {  # type: ignore[attr-defined]
        "spot", "gex_total", "gex_calls", "gex_puts",
        "n_rows", "n_skipped", "regime",
    }


# ---------------------------------------------------------------------------
# gex_regime_multiplier
# ---------------------------------------------------------------------------


def test_multiplier_amplifies_in_positive_gamma():
    assert gex_regime_multiplier("positive_gamma") > 1.0
    assert gex_regime_multiplier("positive_gamma") <= 1.2


def test_multiplier_dampens_in_negative_gamma():
    m = gex_regime_multiplier("negative_gamma")
    assert m < 1.0
    assert m >= 0.5  # never veto outright


def test_multiplier_neutral_is_identity():
    assert gex_regime_multiplier("neutral") == 1.0
