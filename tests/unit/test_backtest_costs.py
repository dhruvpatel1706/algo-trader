"""Slippage + commission models."""

from __future__ import annotations

from decimal import Decimal

import pytest
from src.backtest.costs import DEFAULT_COSTS


def test_slippage_buy_pays_up():
    s = DEFAULT_COSTS.slippage(Decimal("2.00"), "buy")
    assert s == Decimal("0.10")


def test_slippage_sell_gets_hit():
    s = DEFAULT_COSTS.slippage(Decimal("2.00"), "sell")
    assert s == Decimal("-0.10")


def test_commission_equity():
    assert DEFAULT_COSTS.commission(100) == Decimal("0.500")


def test_commission_options():
    assert DEFAULT_COSTS.commission(10, "option") == Decimal("6.50")


def test_commission_unknown_class():
    with pytest.raises(ValueError):
        DEFAULT_COSTS.commission(1, "crypto")
