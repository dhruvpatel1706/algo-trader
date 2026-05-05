"""check_option_order — DTE / collateral / yield / per-trade-cap enforcement."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from src.execution.option_order import (
    OptionContract,
    OptionLeg,
    OptionOrder,
)
from src.risk.option_limits import (
    OptionLimitError,
    OptionLimits,
    check_option_order,
)

TODAY = date(2026, 5, 4)


def _put_contract(strike: str = "450", days: int = 30) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        expiration=TODAY + timedelta(days=days),
        strike=Decimal(strike),
        option_type="put",
    )


def _csp_order(
    strike: str = "450",
    qty: int = 1,
    days: int = 30,
    premium: str = "3.00",
) -> OptionOrder:
    leg = OptionLeg(
        contract=_put_contract(strike=strike, days=days),
        action="sell_to_open",
        quantity=qty,
        limit_price=Decimal(premium),
    )
    return OptionOrder(strategy_kind="cash_secured_put", legs=(leg,))


# ---------------------------------------------------------------------------
# Pass cases
# ---------------------------------------------------------------------------


def test_valid_csp_at_one_pct_equity_passes():
    """1 contract on a $50 strike = $5,000 collateral; max_loss <= 1% of $1M.
    Premium 0.50 / 50 = 1% > 0.5% min yield."""
    order = _csp_order(strike="50", qty=1, days=30, premium="0.50")
    # max_loss = 5000 - 50 = 4950; 1% of 500_000 = 5000 → just fits.
    check_option_order(
        order=order,
        equity=Decimal("500000"),
        open_csp_contracts=0,
        today=TODAY,
    )


# ---------------------------------------------------------------------------
# DTE bounds
# ---------------------------------------------------------------------------


def test_dte_below_min_rejected():
    order = _csp_order(strike="50", days=14, premium="0.50")
    with pytest.raises(OptionLimitError, match="DTE 14 below minimum"):
        check_option_order(
            order=order,
            equity=Decimal("500000"),
            open_csp_contracts=0,
            today=TODAY,
        )


def test_dte_above_max_rejected():
    order = _csp_order(strike="50", days=90, premium="0.50")
    with pytest.raises(OptionLimitError, match="DTE 90 above maximum"):
        check_option_order(
            order=order,
            equity=Decimal("500000"),
            open_csp_contracts=0,
            today=TODAY,
        )


# ---------------------------------------------------------------------------
# CSP collateral
# ---------------------------------------------------------------------------


def test_collateral_above_30pct_rejected():
    """Isolate the collateral cap from the 1%-of-equity per-trade cap.

    The per-trade cap caps ``max_loss_usd`` at 1% of equity, and CSP collateral
    is most of max_loss. So a naive small-equity test trips the per-trade cap
    first. We pick a high equity so per-trade is not the binding constraint,
    but tighten ``max_csp_collateral_pct`` so the CSP collateral cap is.
    """
    # equity = 10M → per-trade cap = 100,000 (max_loss 69,600 fits);
    # collateral_cap = 0.5% * 10M = 50,000; collateral = 70,000 > 50,000 → reject.
    cfg = OptionLimits(max_csp_collateral_pct=Decimal("0.005"))
    order2 = _csp_order(strike="350", qty=2, days=30, premium="2.00")
    with pytest.raises(OptionLimitError, match="collateral"):
        check_option_order(
            order=order2,
            equity=Decimal("10000000"),
            open_csp_contracts=0,
            today=TODAY,
            limits=cfg,
        )


def test_too_many_open_csp_contracts_rejected():
    """6 already open vs max=5 → reject regardless of new size."""
    order = _csp_order(strike="50", qty=1, days=30, premium="0.50")
    with pytest.raises(OptionLimitError, match="contract count"):
        check_option_order(
            order=order,
            equity=Decimal("500000"),
            open_csp_contracts=6,
            today=TODAY,
        )


def test_open_plus_new_exceeds_cap_rejected():
    """5 open + 1 new = 6 > 5 cap."""
    order = _csp_order(strike="50", qty=1, days=30, premium="0.50")
    with pytest.raises(OptionLimitError, match="contract count"):
        check_option_order(
            order=order,
            equity=Decimal("500000"),
            open_csp_contracts=5,
            today=TODAY,
        )


# ---------------------------------------------------------------------------
# Premium yield
# ---------------------------------------------------------------------------


def test_premium_yield_below_min_rejected():
    """Premium 0.05 on $50 strike = 0.001 yield < 0.005 min."""
    order = _csp_order(strike="50", qty=1, days=30, premium="0.05")
    with pytest.raises(OptionLimitError, match="yield"):
        check_option_order(
            order=order,
            equity=Decimal("500000"),
            open_csp_contracts=0,
            today=TODAY,
        )


# ---------------------------------------------------------------------------
# Per-trade cap (1% of equity)
# ---------------------------------------------------------------------------


def test_max_loss_above_per_trade_cap_rejected():
    """Equity 50,000 → 1% cap = 500. CSP at strike $50 = 4,950 max_loss → reject."""
    order = _csp_order(strike="50", qty=1, days=30, premium="0.50")
    with pytest.raises(OptionLimitError, match="per-trade cap"):
        check_option_order(
            order=order,
            equity=Decimal("50000"),
            open_csp_contracts=0,
            today=TODAY,
        )


# ---------------------------------------------------------------------------
# Unbounded max_loss (defense-in-depth — should never reach here normally)
# ---------------------------------------------------------------------------


class _UnboundedOrder:
    """Mimics an OptionOrder API but reports unbounded max_loss_usd, simulating
    a future code path that might forget to validate. The limits layer must
    still refuse it."""

    strategy_kind = "cash_secured_put"

    def __init__(self):
        leg = OptionLeg(
            contract=_put_contract(strike="50", days=30),
            action="sell_to_open",
            quantity=1,
            limit_price=Decimal("0.50"),
        )
        self.legs = (leg,)

    @property
    def max_loss_usd(self) -> Decimal | None:
        return None


def test_unbounded_max_loss_rejected():
    fake = _UnboundedOrder()
    with pytest.raises(OptionLimitError, match="unbounded"):
        check_option_order(
            order=fake,  # type: ignore[arg-type]
            equity=Decimal("500000"),
            open_csp_contracts=0,
            today=TODAY,
        )


def test_zero_equity_rejected():
    order = _csp_order(strike="50", days=30, premium="0.50")
    with pytest.raises(OptionLimitError, match="equity"):
        check_option_order(
            order=order,
            equity=Decimal("0"),
            open_csp_contracts=0,
            today=TODAY,
        )
