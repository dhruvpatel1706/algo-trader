"""Crypto broker adapters.

v1 ships `SimulatedCryptoBroker` (paper-only, no API keys required) as the default.
Real broker adapters (Coinbase Advanced, Alpaca crypto, Binance testnet) are stubbed
in v1: constructors and the public shape are present, but `submit()` raises
NotImplementedError until the operator wires up credentials.

Same `submit(order, token) -> Submission` shape as `src.execution.broker.PaperBroker`.
Crypto trades 24/7, supports fractional quantities, and has wider effective spreads
than equities; the SimulatedCryptoBroker models that with `slippage_bps + spread_bps`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.execution.broker import ApprovalToken
from src.execution.orders import Order, Submission, utcnow


@dataclass(frozen=True, slots=True)
class CryptoFill:
    """Lightweight per-fill record used by SimulatedCryptoBroker for accounting.

    Distinct from `Submission` (which is the broker-side acceptance receipt).
    """

    symbol: str
    side: str
    qty: Decimal
    fill_price: Decimal
    filled_at: datetime
    client_order_id: str


@dataclass(slots=True)
class CryptoPosition:
    """Aggregate position tracked by SimulatedCryptoBroker."""

    symbol: str
    qty: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")


class CryptoBroker(abc.ABC):
    """Crypto-specific broker. Same interface as PaperBroker, different fill semantics.

    Implementations must provide `submit(order, token) -> Submission`. They MAY also
    implement `get_position(symbol)` and `get_positions()` for paper accounting; the
    base class provides empty defaults.
    """

    asset_class: str = "crypto"

    @abc.abstractmethod
    def submit(self, order: Order, token: ApprovalToken) -> Submission:
        """Submit an order. Requires both-gate approval token."""

    def get_position(self, symbol: str) -> CryptoPosition | None:  # pragma: no cover - default
        return None

    def get_positions(self) -> dict[str, CryptoPosition]:  # pragma: no cover - default
        return {}


class SimulatedCryptoBroker(CryptoBroker):
    """Default crypto broker for v1. No external dependency, no API keys.

    Models a single fill per submitted order using a configurable spread + slippage.
    The fill price is derived from the order's `limit_price` (for limit orders) or
    a caller-supplied `mark_price` for market orders. For market orders without a
    mark, callers should pass `mark_price` via `submit(..., mark_price=...)`.

    Effective fill price formula:
        buy:  base * (1 + (spread_bps + slippage_bps) / 1e4)
        sell: base * (1 - (spread_bps + slippage_bps) / 1e4)

    `slippage_bps` defaults to 10 bps (0.10%) and `spread_bps` to 5 bps (0.05%) — both
    conservative for liquid crypto pairs (BTC, ETH) on major venues. Tune up for low-cap.
    """

    def __init__(self, slippage_bps: float = 10.0, spread_bps: float = 5.0) -> None:
        if slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative")
        if spread_bps < 0:
            raise ValueError("spread_bps must be non-negative")
        self.slippage_bps = float(slippage_bps)
        self.spread_bps = float(spread_bps)
        self._positions: dict[str, CryptoPosition] = {}
        self._fills: list[CryptoFill] = []
        self._counter: int = 0

    def submit(
        self,
        order: Order,
        token: ApprovalToken,
        *,
        mark_price: Decimal | float | None = None,
    ) -> Submission:
        """Simulate an immediate fill. `mark_price` is used for market orders.

        For limit orders we use `order.limit_price` as the reference (assume the
        limit is marketable, since this is paper-only and we model immediate fills).
        """
        if not token.risk_reason or not token.compliance_reason:
            raise PermissionError("ApprovalToken missing gate reason")

        base = self._reference_price(order, mark_price)
        fill_price = self._apply_costs(base, order.side)
        qty = Decimal(str(order.qty))

        self._update_position(order.symbol, order.side, qty, fill_price)
        now = utcnow()
        self._fills.append(
            CryptoFill(
                symbol=order.symbol,
                side=order.side,
                qty=qty,
                fill_price=fill_price,
                filled_at=now,
                client_order_id=order.client_order_id,
            )
        )

        self._counter += 1
        return Submission(
            broker_order_id=f"sim-crypto-{self._counter}",
            client_order_id=order.client_order_id,
            accepted_at=now,
            status="filled",
        )

    def get_position(self, symbol: str) -> CryptoPosition | None:
        return self._positions.get(symbol)

    def get_positions(self) -> dict[str, CryptoPosition]:
        return dict(self._positions)

    @property
    def fills(self) -> list[CryptoFill]:
        return list(self._fills)

    def _reference_price(
        self, order: Order, mark_price: Decimal | float | None
    ) -> Decimal:
        if order.order_type == "limit":
            if order.limit_price is None:  # pragma: no cover - guarded by Order.__post_init__
                raise ValueError("limit order missing limit_price")
            return Decimal(str(order.limit_price))
        if mark_price is None:
            raise ValueError(
                "SimulatedCryptoBroker requires mark_price for market orders"
            )
        return Decimal(str(mark_price))

    def _apply_costs(self, base: Decimal, side: str) -> Decimal:
        bps = Decimal(str(self.slippage_bps + self.spread_bps)) / Decimal("10000")
        if side == "buy":
            return base * (Decimal("1") + bps)
        return base * (Decimal("1") - bps)

    def _update_position(
        self, symbol: str, side: str, qty: Decimal, price: Decimal
    ) -> None:
        pos = self._positions.get(symbol) or CryptoPosition(symbol=symbol)
        if side == "buy":
            new_qty = pos.qty + qty
            if new_qty > 0:
                pos.avg_price = (
                    (pos.avg_price * pos.qty) + (price * qty)
                ) / new_qty
            pos.qty = new_qty
        else:  # sell
            pos.qty = pos.qty - qty
            if pos.qty == 0:
                pos.avg_price = Decimal("0")
        self._positions[symbol] = pos


class _RealBrokerStubMixin:
    """Shared scaffolding for not-yet-wired real brokers."""

    name: str = "real-broker-stub"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 **_: Any) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        # We deliberately do NOT import SDKs here. Stub stays import-cheap.

    def _check_credentials(self) -> None:
        if not self.api_key or not self.api_secret:
            raise NotImplementedError(
                f"{self.name}: real-broker integration not wired. "
                "Provide api_key + api_secret and implement submit()."
            )


class CoinbaseAdvancedBroker(_RealBrokerStubMixin, CryptoBroker):
    """US-friendly real crypto broker. Stub in v1.

    When implemented, will use coinbase-advanced-py SDK or HTTP REST against
    https://api.coinbase.com/api/v3/brokerage/orders.
    """

    name = "coinbase-advanced"

    def submit(self, order: Order, token: ApprovalToken) -> Submission:
        self._check_credentials()
        raise NotImplementedError(
            "CoinbaseAdvancedBroker.submit() is a v1 stub. "
            "Implement order placement against the brokerage REST API."
        )


class AlpacaCryptoBroker(_RealBrokerStubMixin, CryptoBroker):
    """US-friendly real crypto broker via Alpaca crypto endpoints. Stub in v1."""

    name = "alpaca-crypto"

    def submit(self, order: Order, token: ApprovalToken) -> Submission:
        self._check_credentials()
        raise NotImplementedError(
            "AlpacaCryptoBroker.submit() is a v1 stub. "
            "Implement using alpaca.trading.client crypto routes."
        )


class BinanceTestnetBroker(_RealBrokerStubMixin, CryptoBroker):
    """Non-US users only. Binance spot testnet (testnet.binance.vision). Stub in v1."""

    name = "binance-testnet"

    def submit(self, order: Order, token: ApprovalToken) -> Submission:
        self._check_credentials()
        raise NotImplementedError(
            "BinanceTestnetBroker.submit() is a v1 stub. "
            "Implement against https://testnet.binance.vision/api."
        )


_BROKERS: dict[str, type[CryptoBroker]] = {
    "simulated": SimulatedCryptoBroker,
    "coinbase": CoinbaseAdvancedBroker,
    "coinbase-advanced": CoinbaseAdvancedBroker,
    "alpaca": AlpacaCryptoBroker,
    "alpaca-crypto": AlpacaCryptoBroker,
    "binance": BinanceTestnetBroker,
    "binance-testnet": BinanceTestnetBroker,
}


def make_crypto_broker(name: str = "simulated", **kwargs: Any) -> CryptoBroker:
    """Factory. Default 'simulated' works without any API keys.

    Examples
    --------
    >>> broker = make_crypto_broker()  # SimulatedCryptoBroker, default costs
    >>> broker = make_crypto_broker("simulated", slippage_bps=15, spread_bps=10)
    >>> broker = make_crypto_broker("coinbase", api_key="...", api_secret="...")
    """
    key = name.lower().strip()
    cls = _BROKERS.get(key)
    if cls is None:
        raise ValueError(
            f"unknown crypto broker {name!r}; "
            f"choose from {sorted(set(_BROKERS))}"
        )
    return cls(**kwargs)


__all__ = [
    "AlpacaCryptoBroker",
    "BinanceTestnetBroker",
    "CoinbaseAdvancedBroker",
    "CryptoBroker",
    "CryptoFill",
    "CryptoPosition",
    "SimulatedCryptoBroker",
    "make_crypto_broker",
]
