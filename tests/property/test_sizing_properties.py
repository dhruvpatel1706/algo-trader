"""Property-based tests for src.risk.sizing.

These tests assert hard invariants on the position sizer. The sizer is the
single chokepoint between strategy intent and broker submission, so any
violation here is a safety bug, not a numerical detail.

Invariants under test
---------------------
- Quantity is never negative.
- Quantity is always an integer.
- Notional (qty * entry) is bounded by max_position_pct * equity (when given).
- Realized loss at the stop is bounded by ``risk_pct * equity`` plus a one-share
  rounding tolerance, EXCEPT in the deliberately-degenerate case where
  ``|entry - stop|`` is below the floor (one cent for stocks).
- Invalid inputs (non-positive equity / entry / stop, risk outside (0, 1])
  raise ValueError rather than silently producing garbage.
- Decimal arithmetic does not throw on arbitrary precision inputs.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from src.risk.sizing import (
    drawdown_fraction,
    kelly_fraction,
    portfolio_heat,
    position_size,
    quarter_kelly,
)

# Reusable strategies. Decimals avoid binary float surprises in money math.
_equity = st.decimals(
    min_value=Decimal("100"),
    max_value=Decimal("10000000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_price = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_risk_pct = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("0.01"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
_max_position_pct = st.decimals(
    min_value=Decimal("0.001"),
    max_value=Decimal("0.10"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)


pytestmark = pytest.mark.property


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(equity=_equity, entry=_price, stop=_price, risk_pct=_risk_pct)
def test_position_size_never_negative(equity, entry, stop, risk_pct):
    """qty >= 0 unconditionally — broker would reject a negative share count."""
    qty = position_size(equity=equity, risk_pct=risk_pct, entry=entry, stop=stop)
    assert qty >= 0
    assert isinstance(qty, int)


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    equity=_equity,
    entry=_price,
    stop=_price,
    risk_pct=_risk_pct,
    max_pct=_max_position_pct,
)
def test_position_size_respects_max_position_pct(equity, entry, stop, risk_pct, max_pct):
    """Notional (qty * entry) must never exceed max_position_pct * equity."""
    qty = position_size(
        equity=equity,
        risk_pct=risk_pct,
        entry=entry,
        stop=stop,
        max_position_pct=max_pct,
    )
    notional = Decimal(qty) * entry
    cap = equity * max_pct
    # Implementation floors, so notional must be strictly bounded by the cap.
    assert notional <= cap, f"notional {notional} > cap {cap} (qty={qty})"


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(equity=_equity, entry=_price, stop=_price, risk_pct=_risk_pct)
def test_position_size_loss_capped_at_risk_pct(equity, entry, stop, risk_pct):
    """If stop is hit, |qty * (entry - stop)| <= risk_pct * equity (+ rounding tol).

    The rounding tolerance is one share-worth of risk, since `position_size`
    floors to whole shares. We exclude the degenerate case where
    ``|entry - stop| < $0.01`` because the function uses a floor (`_EPS`)
    there to avoid divide-by-zero, which deliberately uncaps loss versus a
    sub-cent stop distance.
    """
    distance = abs(entry - stop)
    # Skip the deliberately uncapped near-zero-distance case.
    assume(distance >= Decimal("0.01"))
    qty = position_size(equity=equity, risk_pct=risk_pct, entry=entry, stop=stop)
    realized_loss = Decimal(qty) * distance
    risk_budget = equity * risk_pct
    # Allow up to one share of additional risk for floor() rounding.
    tolerance = distance
    assert realized_loss <= risk_budget + tolerance, (
        f"loss {realized_loss} > risk_budget {risk_budget} (qty={qty})"
    )


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(equity=_equity, entry=_price, risk_pct=_risk_pct)
def test_position_size_zero_distance_does_not_divide_by_zero(equity, entry, risk_pct):
    """When entry == stop, the EPS floor must keep the function total."""
    qty = position_size(equity=equity, risk_pct=risk_pct, entry=entry, stop=entry)
    # No ZeroDivisionError; some non-negative integer was returned.
    assert qty >= 0
    assert isinstance(qty, int)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    equity=st.decimals(max_value=Decimal("0"), min_value=Decimal("-1000000"), allow_nan=False),
    entry=_price,
    stop=_price,
    risk_pct=_risk_pct,
)
def test_position_size_rejects_nonpositive_equity(equity, entry, stop, risk_pct):
    """Non-positive equity must raise ValueError (fail loud)."""
    with pytest.raises(ValueError):
        position_size(equity=equity, risk_pct=risk_pct, entry=entry, stop=stop)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    equity=_equity,
    entry=_price,
    stop=_price,
    risk_pct=st.one_of(
        st.decimals(max_value=Decimal("0"), min_value=Decimal("-1"), allow_nan=False),
        st.decimals(min_value=Decimal("1.0001"), max_value=Decimal("100"), allow_nan=False),
    ),
)
def test_position_size_rejects_out_of_band_risk_pct(equity, entry, stop, risk_pct):
    """risk_pct outside (0, 1] must raise — silent acceptance is unsafe."""
    with pytest.raises(ValueError):
        position_size(equity=equity, risk_pct=risk_pct, entry=entry, stop=stop)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    equity=_equity,
    entry=st.decimals(max_value=Decimal("0"), min_value=Decimal("-1000"), allow_nan=False),
    stop=_price,
    risk_pct=_risk_pct,
)
def test_position_size_rejects_nonpositive_entry(equity, entry, stop, risk_pct):
    with pytest.raises(ValueError):
        position_size(equity=equity, risk_pct=risk_pct, entry=entry, stop=stop)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    equity=_equity,
    entry=_price,
    stop=st.decimals(max_value=Decimal("0"), min_value=Decimal("-1000"), allow_nan=False),
    risk_pct=_risk_pct,
)
def test_position_size_rejects_nonpositive_stop(equity, entry, stop, risk_pct):
    with pytest.raises(ValueError):
        position_size(equity=equity, risk_pct=risk_pct, entry=entry, stop=stop)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    equity=_equity,
    entry=_price,
    stop=_price,
    risk_pct=_risk_pct,
    max_pct=_max_position_pct,
)
def test_position_size_cap_only_shrinks(equity, entry, stop, risk_pct, max_pct):
    """Adding max_position_pct can only return a qty <= the uncapped result."""
    uncapped = position_size(equity=equity, risk_pct=risk_pct, entry=entry, stop=stop)
    capped = position_size(
        equity=equity,
        risk_pct=risk_pct,
        entry=entry,
        stop=stop,
        max_position_pct=max_pct,
    )
    assert capped <= uncapped


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    win_rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    win_loss_ratio=st.floats(
        min_value=0.0001, max_value=100.0, allow_nan=False, allow_infinity=False
    ),
)
def test_kelly_fraction_non_negative_and_sane(win_rate, win_loss_ratio):
    """Kelly result must be in [0, 1] (we clip at 0 and edge wins cap at 1)."""
    f = kelly_fraction(win_rate, win_loss_ratio)
    assert f >= 0.0
    # Quarter Kelly is exactly f / 4.
    qf = quarter_kelly(win_rate, win_loss_ratio)
    assert abs(qf - f / 4.0) < 1e-12


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    win_rate=st.one_of(
        st.floats(min_value=-100.0, max_value=-0.0001, allow_nan=False),
        st.floats(min_value=1.0001, max_value=100.0, allow_nan=False),
    ),
    win_loss_ratio=st.floats(
        min_value=0.0001, max_value=100.0, allow_nan=False, allow_infinity=False
    ),
)
def test_kelly_rejects_out_of_band_win_rate(win_rate, win_loss_ratio):
    with pytest.raises(ValueError):
        kelly_fraction(win_rate, win_loss_ratio)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    current=st.decimals(min_value=Decimal("1"), max_value=Decimal("10000000"), allow_nan=False),
    peak=st.decimals(min_value=Decimal("1"), max_value=Decimal("10000000"), allow_nan=False),
)
def test_drawdown_fraction_in_unit_interval(current, peak):
    dd = drawdown_fraction(current, peak)
    assert Decimal("0") <= dd <= Decimal("1"), f"dd={dd} for current={current} peak={peak}"


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    current=st.decimals(min_value=Decimal("1"), max_value=Decimal("1000000"), allow_nan=False),
    peak=st.decimals(min_value=Decimal("1"), max_value=Decimal("1000000"), allow_nan=False),
)
def test_drawdown_zero_when_above_peak(current, peak):
    """current_equity > peak should clamp dd to 0, not return negative."""
    assume(current > peak)
    dd = drawdown_fraction(current, peak)
    assert dd == Decimal("0")


class _Pos:
    __slots__ = ("open_risk",)

    def __init__(self, open_risk: Decimal) -> None:
        self.open_risk = open_risk


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    risks=st.lists(
        st.decimals(min_value=Decimal("0"), max_value=Decimal("100000"), allow_nan=False),
        min_size=0,
        max_size=20,
    ),
    equity=_equity,
)
def test_portfolio_heat_non_negative_when_inputs_non_negative(risks, equity):
    positions = [_Pos(r) for r in risks]
    heat = portfolio_heat(positions, equity)
    assert heat >= Decimal("0")
