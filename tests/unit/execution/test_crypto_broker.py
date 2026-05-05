"""SimulatedCryptoBroker behavior + real-broker stub guards + factory."""

from __future__ import annotations

from decimal import Decimal

import pytest
from src.execution.broker import approval_token
from src.execution.crypto_broker import (
    AlpacaCryptoBroker,
    BinanceTestnetBroker,
    CoinbaseAdvancedBroker,
    CryptoBroker,
    SimulatedCryptoBroker,
    make_crypto_broker,
)
from src.execution.orders import Order
from src.risk.limits import Decision


def _approval(cycle_id: str = "c1"):
    return approval_token(
        cycle_id=cycle_id,
        risk=Decision(True, "qty=1 within all caps", adjusted_size=1),
        compliance=Decision(True, "all checks pass"),
    )


def _limit_order(symbol: str = "BTCUSDT", side: str = "buy", qty: int = 1, price: str = "50000"):
    return Order(
        client_order_id=f"01H_TEST_CRYPTO_{symbol}_{side.upper()}",
        symbol=symbol,
        qty=qty,
        side=side,
        order_type="limit",
        time_in_force="gtc",
        limit_price=Decimal(price),
    )


# ---------------------------------------------------------------------------
# SimulatedCryptoBroker
# ---------------------------------------------------------------------------


def test_simulated_buy_returns_fill_with_slippage_above_entry():
    broker = SimulatedCryptoBroker(slippage_bps=10.0, spread_bps=5.0)
    order = _limit_order(side="buy", price="50000")
    sub = broker.submit(order, _approval())

    assert sub.client_order_id == order.client_order_id
    assert sub.broker_order_id.startswith("sim-crypto-")
    assert sub.status == "filled"

    fills = broker.fills
    assert len(fills) == 1
    fill = fills[0]
    # buy fill should be entry * (1 + 15bps) = 50000 * 1.0015 = 50075
    expected = Decimal("50000") * (Decimal("1") + Decimal("15") / Decimal("10000"))
    assert fill.fill_price == expected
    assert fill.fill_price > Decimal("50000")
    assert fill.qty == Decimal("1")


def test_simulated_sell_fill_below_entry():
    broker = SimulatedCryptoBroker(slippage_bps=10.0, spread_bps=5.0)
    order = _limit_order(side="sell", price="50000")
    broker.submit(order, _approval())
    fill = broker.fills[0]
    expected = Decimal("50000") * (Decimal("1") - Decimal("15") / Decimal("10000"))
    assert fill.fill_price == expected
    assert fill.fill_price < Decimal("50000")


def test_simulated_tracks_open_positions():
    broker = SimulatedCryptoBroker()
    broker.submit(_limit_order(symbol="BTCUSDT", qty=2, price="50000"), _approval())
    broker.submit(_limit_order(symbol="ETHUSDT", qty=3, price="3000"), _approval("c2"))

    btc = broker.get_position("BTCUSDT")
    eth = broker.get_position("ETHUSDT")
    assert btc is not None and btc.qty == Decimal("2")
    assert eth is not None and eth.qty == Decimal("3")
    assert set(broker.get_positions().keys()) == {"BTCUSDT", "ETHUSDT"}


def test_simulated_position_zeroes_after_round_trip():
    broker = SimulatedCryptoBroker()
    broker.submit(
        _limit_order(symbol="BTCUSDT", side="buy", qty=2, price="50000"), _approval("c1")
    )
    broker.submit(
        _limit_order(symbol="BTCUSDT", side="sell", qty=2, price="51000"), _approval("c2")
    )
    pos = broker.get_position("BTCUSDT")
    assert pos is not None
    assert pos.qty == Decimal("0")
    assert pos.avg_price == Decimal("0")


def test_simulated_market_order_requires_mark_price():
    broker = SimulatedCryptoBroker()
    market = Order(
        client_order_id="01H_MARKET_TEST_AAAAAAAAAAAA",
        symbol="BTCUSDT",
        qty=1,
        side="buy",
        order_type="market",
        time_in_force="gtc",
    )
    with pytest.raises(ValueError, match="mark_price"):
        broker.submit(market, _approval())

    sub = broker.submit(market, _approval("c2"), mark_price=Decimal("50000"))
    assert sub.status == "filled"


def test_simulated_rejects_negative_costs():
    with pytest.raises(ValueError):
        SimulatedCryptoBroker(slippage_bps=-1.0)
    with pytest.raises(ValueError):
        SimulatedCryptoBroker(spread_bps=-1.0)


# ---------------------------------------------------------------------------
# Real-broker stubs
# ---------------------------------------------------------------------------


def test_coinbase_advanced_without_api_key_raises_not_implemented():
    broker = CoinbaseAdvancedBroker()
    with pytest.raises(NotImplementedError):
        broker.submit(_limit_order(), _approval())


def test_alpaca_crypto_without_api_key_raises_not_implemented():
    broker = AlpacaCryptoBroker()
    with pytest.raises(NotImplementedError):
        broker.submit(_limit_order(), _approval())


def test_binance_testnet_without_api_key_raises_not_implemented():
    broker = BinanceTestnetBroker()
    with pytest.raises(NotImplementedError):
        broker.submit(_limit_order(), _approval())


def test_coinbase_with_credentials_still_stubbed_in_v1():
    broker = CoinbaseAdvancedBroker(api_key="k", api_secret="s")
    with pytest.raises(NotImplementedError, match="stub"):
        broker.submit(_limit_order(), _approval())


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_default_returns_simulated():
    b = make_crypto_broker()
    assert isinstance(b, SimulatedCryptoBroker)
    assert isinstance(b, CryptoBroker)


def test_factory_simulated_with_kwargs():
    b = make_crypto_broker("simulated", slippage_bps=20, spread_bps=10)
    assert isinstance(b, SimulatedCryptoBroker)
    assert b.slippage_bps == 20.0
    assert b.spread_bps == 10.0


def test_factory_real_brokers_resolve_to_correct_class():
    assert isinstance(make_crypto_broker("coinbase"), CoinbaseAdvancedBroker)
    assert isinstance(make_crypto_broker("alpaca"), AlpacaCryptoBroker)
    assert isinstance(make_crypto_broker("binance"), BinanceTestnetBroker)


def test_factory_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown crypto broker"):
        make_crypto_broker("nope")
