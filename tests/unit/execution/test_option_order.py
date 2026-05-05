"""OptionContract / OptionLeg / OptionOrder validation + max_loss_usd math."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from src.execution.option_order import (
    CONTRACT_MULTIPLIER,
    OptionContract,
    OptionLeg,
    OptionOrder,
)


def _expiry(days: int = 30) -> date:
    return date.today() + timedelta(days=days)


def _spy_call(strike: str = "500", days: int = 30) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        expiration=_expiry(days),
        strike=Decimal(strike),
        option_type="call",
    )


def _spy_put(strike: str = "450", days: int = 30) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        expiration=_expiry(days),
        strike=Decimal(strike),
        option_type="put",
    )


# ---------------------------------------------------------------------------
# OptionContract
# ---------------------------------------------------------------------------


def test_dte_today_is_zero():
    c = OptionContract(
        underlying="SPY",
        expiration=date.today(),
        strike=Decimal("500"),
        option_type="call",
    )
    assert c.dte == 0


def test_dte_30_days_out():
    c = _spy_call(days=30)
    assert c.dte == 30


def test_contract_rejects_empty_underlying():
    with pytest.raises(ValueError):
        OptionContract(
            underlying="",
            expiration=_expiry(),
            strike=Decimal("100"),
            option_type="call",
        )


def test_contract_rejects_nonpositive_strike():
    with pytest.raises(ValueError):
        OptionContract(
            underlying="SPY",
            expiration=_expiry(),
            strike=Decimal("0"),
            option_type="call",
        )


# ---------------------------------------------------------------------------
# OptionLeg
# ---------------------------------------------------------------------------


def test_leg_constructs_cleanly():
    leg = OptionLeg(
        contract=_spy_put(),
        action="sell_to_open",
        quantity=1,
        limit_price=Decimal("3.50"),
    )
    assert leg.quantity == 1
    assert leg.limit_price == Decimal("3.50")


def test_leg_rejects_zero_quantity():
    with pytest.raises(ValueError):
        OptionLeg(contract=_spy_put(), action="sell_to_open", quantity=0)


def test_leg_rejects_negative_quantity():
    with pytest.raises(ValueError):
        OptionLeg(contract=_spy_put(), action="sell_to_open", quantity=-1)


def test_leg_rejects_nonpositive_limit_price():
    with pytest.raises(ValueError):
        OptionLeg(
            contract=_spy_put(),
            action="sell_to_open",
            quantity=1,
            limit_price=Decimal("-0.01"),
        )


# ---------------------------------------------------------------------------
# OptionOrder — happy paths
# ---------------------------------------------------------------------------


def test_valid_csp_passes():
    leg = OptionLeg(
        contract=_spy_put(strike="450"),
        action="sell_to_open",
        quantity=1,
        limit_price=Decimal("3.00"),
    )
    order = OptionOrder(strategy_kind="cash_secured_put", legs=(leg,))
    assert order.strategy_kind == "cash_secured_put"
    assert order.max_loss_usd is not None


def test_valid_covered_call_with_sufficient_underlying_passes():
    leg = OptionLeg(
        contract=_spy_call(strike="510"),
        action="sell_to_open",
        quantity=1,
        limit_price=Decimal("2.50"),
    )
    order = OptionOrder(
        strategy_kind="covered_call",
        legs=(leg,),
        underlying_position_qty=100,
    )
    assert order.underlying_position_qty == 100


def test_protective_put_passes():
    leg = OptionLeg(
        contract=_spy_put(strike="490"),
        action="buy_to_open",
        quantity=1,
        limit_price=Decimal("4.00"),
    )
    order = OptionOrder(
        strategy_kind="protective_put",
        legs=(leg,),
        underlying_position_qty=100,
    )
    assert order.strategy_kind == "protective_put"


# ---------------------------------------------------------------------------
# OptionOrder — rejection paths (NO NAKED OPTIONS)
# ---------------------------------------------------------------------------


def test_naked_call_rejected():
    """sell_to_open call without sufficient underlying = naked call."""
    leg = OptionLeg(
        contract=_spy_call(),
        action="sell_to_open",
        quantity=1,
        limit_price=Decimal("3.00"),
    )
    with pytest.raises(ValueError, match=r"naked|underlying"):
        OptionOrder(
            strategy_kind="covered_call",
            legs=(leg,),
            underlying_position_qty=0,
        )


def test_covered_call_partial_underlying_rejected():
    """99 shares is not enough to cover 1 call (need 100)."""
    leg = OptionLeg(
        contract=_spy_call(),
        action="sell_to_open",
        quantity=1,
        limit_price=Decimal("3.00"),
    )
    with pytest.raises(ValueError):
        OptionOrder(
            strategy_kind="covered_call",
            legs=(leg,),
            underlying_position_qty=99,
        )


def test_csp_buy_to_open_rejected():
    """CSP must be sell_to_open."""
    leg = OptionLeg(
        contract=_spy_put(),
        action="buy_to_open",
        quantity=1,
        limit_price=Decimal("3.00"),
    )
    with pytest.raises(ValueError):
        OptionOrder(strategy_kind="cash_secured_put", legs=(leg,))


def test_csp_with_call_rejected():
    leg = OptionLeg(
        contract=_spy_call(),
        action="sell_to_open",
        quantity=1,
        limit_price=Decimal("3.00"),
    )
    with pytest.raises(ValueError):
        OptionOrder(strategy_kind="cash_secured_put", legs=(leg,))


def test_protective_put_without_underlying_rejected():
    leg = OptionLeg(
        contract=_spy_put(),
        action="buy_to_open",
        quantity=1,
        limit_price=Decimal("3.00"),
    )
    with pytest.raises(ValueError):
        OptionOrder(
            strategy_kind="protective_put",
            legs=(leg,),
            underlying_position_qty=50,
        )


def test_empty_legs_rejected():
    with pytest.raises(ValueError):
        OptionOrder(strategy_kind="cash_secured_put", legs=())


# ---------------------------------------------------------------------------
# max_loss_usd — bounded for every allowed strategy
# ---------------------------------------------------------------------------


def test_csp_max_loss_is_collateral_minus_premium():
    leg = OptionLeg(
        contract=_spy_put(strike="450"),
        action="sell_to_open",
        quantity=1,
        limit_price=Decimal("3.00"),
    )
    order = OptionOrder(strategy_kind="cash_secured_put", legs=(leg,))
    # collateral = 450 * 100 = 45,000; premium = 3 * 100 = 300; max_loss = 44,700
    expected = Decimal("450") * Decimal(CONTRACT_MULTIPLIER) - Decimal("3.00") * Decimal(
        CONTRACT_MULTIPLIER
    )
    assert order.max_loss_usd == expected
    assert order.max_loss_usd is not None


def test_covered_call_max_loss_is_strike_notional_net_premium():
    leg = OptionLeg(
        contract=_spy_call(strike="510"),
        action="sell_to_open",
        quantity=1,
        limit_price=Decimal("2.50"),
    )
    order = OptionOrder(
        strategy_kind="covered_call",
        legs=(leg,),
        underlying_position_qty=100,
    )
    expected = Decimal("510") * Decimal(CONTRACT_MULTIPLIER) - Decimal("2.50") * Decimal(
        CONTRACT_MULTIPLIER
    )
    assert order.max_loss_usd == expected


def test_protective_put_max_loss_is_premium_only():
    leg = OptionLeg(
        contract=_spy_put(strike="490"),
        action="buy_to_open",
        quantity=2,
        limit_price=Decimal("4.00"),
    )
    order = OptionOrder(
        strategy_kind="protective_put",
        legs=(leg,),
        underlying_position_qty=200,
    )
    # premium-paid = 4 * 100 * 2 = 800
    assert order.max_loss_usd == Decimal("4.00") * Decimal(CONTRACT_MULTIPLIER) * Decimal(2)


def test_max_loss_bounded_for_all_three_strategies():
    csp = OptionOrder(
        strategy_kind="cash_secured_put",
        legs=(
            OptionLeg(
                contract=_spy_put(strike="100"),
                action="sell_to_open",
                quantity=1,
                limit_price=Decimal("1.00"),
            ),
        ),
    )
    cc = OptionOrder(
        strategy_kind="covered_call",
        legs=(
            OptionLeg(
                contract=_spy_call(strike="100"),
                action="sell_to_open",
                quantity=1,
                limit_price=Decimal("1.00"),
            ),
        ),
        underlying_position_qty=100,
    )
    pp = OptionOrder(
        strategy_kind="protective_put",
        legs=(
            OptionLeg(
                contract=_spy_put(strike="100"),
                action="buy_to_open",
                quantity=1,
                limit_price=Decimal("1.00"),
            ),
        ),
        underlying_position_qty=100,
    )
    for order in (csp, cc, pp):
        assert order.max_loss_usd is not None
        assert order.max_loss_usd > 0
