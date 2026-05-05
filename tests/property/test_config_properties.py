"""Property-based tests for src.config validators.

Risk caps in Settings are the static safety boundary of the system: if a
validator silently accepts an out-of-band value, the live trader can be
configured to risk more than v1 design allows. The validators MUST hold:

- MAX_PER_TRADE_RISK accepted only in (0, 0.01].
- MAX_PORTFOLIO_HEAT accepted only in (0, 0.06].
- MAX_SINGLE_POSITION accepted only in (0, 0.10].
- DAILY_LOSS_HALT accepted only in [-0.10, 0).
- DRAWDOWN_HALT accepted only in (0, 0.30].
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from src.config import Settings

pytestmark = pytest.mark.property


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    v=st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("0.01"), allow_nan=False)
)
def test_max_per_trade_risk_accepts_in_band(v):
    s = Settings(MAX_PER_TRADE_RISK=v)
    assert s.MAX_PER_TRADE_RISK == v


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    v=st.one_of(
        st.decimals(max_value=Decimal("0"), min_value=Decimal("-1"), allow_nan=False),
        st.decimals(min_value=Decimal("0.0101"), max_value=Decimal("100"), allow_nan=False),
    )
)
def test_max_per_trade_risk_rejects_out_of_band(v):
    with pytest.raises(ValidationError):
        Settings(MAX_PER_TRADE_RISK=v)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(v=st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("0.06"), allow_nan=False))
def test_max_portfolio_heat_accepts_in_band(v):
    s = Settings(MAX_PORTFOLIO_HEAT=v)
    assert s.MAX_PORTFOLIO_HEAT == v


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    v=st.one_of(
        st.decimals(max_value=Decimal("0"), min_value=Decimal("-1"), allow_nan=False),
        st.decimals(min_value=Decimal("0.0601"), max_value=Decimal("100"), allow_nan=False),
    )
)
def test_max_portfolio_heat_rejects_out_of_band(v):
    with pytest.raises(ValidationError):
        Settings(MAX_PORTFOLIO_HEAT=v)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(v=st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("0.10"), allow_nan=False))
def test_max_single_position_accepts_in_band(v):
    s = Settings(MAX_SINGLE_POSITION=v)
    assert s.MAX_SINGLE_POSITION == v


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    v=st.one_of(
        st.decimals(max_value=Decimal("0"), min_value=Decimal("-1"), allow_nan=False),
        st.decimals(min_value=Decimal("0.1001"), max_value=Decimal("100"), allow_nan=False),
    )
)
def test_max_single_position_rejects_out_of_band(v):
    with pytest.raises(ValidationError):
        Settings(MAX_SINGLE_POSITION=v)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(v=st.decimals(min_value=Decimal("-0.10"), max_value=Decimal("-0.0001"), allow_nan=False))
def test_daily_loss_halt_accepts_in_band(v):
    s = Settings(DAILY_LOSS_HALT=v)
    assert s.DAILY_LOSS_HALT == v


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    v=st.one_of(
        st.decimals(max_value=Decimal("-0.1001"), min_value=Decimal("-100"), allow_nan=False),
        st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), allow_nan=False),
    )
)
def test_daily_loss_halt_rejects_out_of_band(v):
    with pytest.raises(ValidationError):
        Settings(DAILY_LOSS_HALT=v)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(v=st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("0.30"), allow_nan=False))
def test_drawdown_halt_accepts_in_band(v):
    s = Settings(DRAWDOWN_HALT=v)
    assert s.DRAWDOWN_HALT == v


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    v=st.one_of(
        st.decimals(max_value=Decimal("0"), min_value=Decimal("-1"), allow_nan=False),
        st.decimals(min_value=Decimal("0.3001"), max_value=Decimal("100"), allow_nan=False),
    )
)
def test_drawdown_halt_rejects_out_of_band(v):
    with pytest.raises(ValidationError):
        Settings(DRAWDOWN_HALT=v)
