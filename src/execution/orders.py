"""Order DTO + idempotent client_order_id generator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import ulid


@dataclass(frozen=True, slots=True)
class Order:
    """Broker-bound order. `client_order_id` is the idempotency key."""

    client_order_id: str
    symbol: str
    qty: int
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    time_in_force: Literal["day", "gtc", "ioc", "fok"]
    limit_price: Decimal | None = None
    extended_hours: bool = False
    strategy_tag: str = ""

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError("qty must be positive")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("market order must not include limit_price")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit_price must be positive")


@dataclass(frozen=True, slots=True)
class Submission:
    """Result of a successful broker submit."""

    broker_order_id: str
    client_order_id: str
    accepted_at: datetime
    status: str


def new_client_order_id() -> str:
    """ULID — sortable, 26 chars, base32. Use as the broker idempotency key."""
    return str(ulid.ULID())


def utcnow() -> datetime:
    return datetime.now(UTC)
