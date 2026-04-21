"""Order DTO + ULID."""

from __future__ import annotations

from decimal import Decimal

import pytest
from src.execution.orders import Order, new_client_order_id


def _order(**kw):
    defaults = dict(
        client_order_id="01H123",
        symbol="SPY",
        qty=10,
        side="buy",
        order_type="market",
        time_in_force="day",
    )
    defaults.update(kw)
    return Order(**defaults)


def test_market_order_ok():
    assert _order().qty == 10


def test_limit_order_requires_price():
    with pytest.raises(ValueError):
        _order(order_type="limit")


def test_limit_order_with_price_ok():
    o = _order(order_type="limit", limit_price=Decimal("100.50"))
    assert o.limit_price == Decimal("100.50")


def test_market_order_rejects_price():
    with pytest.raises(ValueError):
        _order(order_type="market", limit_price=Decimal("100.50"))


@pytest.mark.parametrize("qty", [-1, 0])
def test_invalid_qty_rejected(qty):
    with pytest.raises(ValueError):
        _order(qty=qty)


def test_negative_limit_price_rejected():
    with pytest.raises(ValueError):
        _order(order_type="limit", limit_price=Decimal("-1"))


def test_client_order_ids_unique():
    ids = {new_client_order_id() for _ in range(100)}
    assert len(ids) == 100


def test_client_order_id_is_ulid_length():
    assert len(new_client_order_id()) == 26
