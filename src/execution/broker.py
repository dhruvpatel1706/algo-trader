"""Broker adapters. v1 ships `PaperBroker` (Alpaca paper). `LiveBroker` is a stub."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn, Protocol

from src.config import get_settings
from src.execution.orders import Order, Submission, utcnow
from src.risk.limits import Decision


class _AlpacaTradingClient(Protocol):
    """Minimal protocol for the alpaca-py TradingClient surface we use.

    Defined as a Protocol so tests can supply a fake without importing alpaca-py.
    """

    def submit_order(self, order_data): ...
    def get_account(self): ...


@dataclass(frozen=True, slots=True)
class ApprovalToken:
    """Proof that both gates approved. Construct only via `approval_token()`."""

    cycle_id: str
    risk_decision_ts: datetime
    compliance_decision_ts: datetime
    risk_reason: str
    compliance_reason: str


def approval_token(
    cycle_id: str,
    risk: Decision,
    compliance: Decision,
    risk_ts: datetime | None = None,
    compliance_ts: datetime | None = None,
) -> ApprovalToken:
    """Factory enforces both-gate APPROVE. Raises if either rejected or empty."""
    if not risk.approve:
        raise PermissionError(f"risk gate rejected: {risk.reason}")
    if not compliance.approve:
        raise PermissionError(f"compliance gate rejected: {compliance.reason}")
    if not risk.reason or not compliance.reason:
        raise PermissionError("decision reasons must be populated for audit")
    return ApprovalToken(
        cycle_id=cycle_id,
        risk_decision_ts=risk_ts or utcnow(),
        compliance_decision_ts=compliance_ts or utcnow(),
        risk_reason=risk.reason,
        compliance_reason=compliance.reason,
    )


class PaperBroker:
    """Alpaca paper-trading broker. Single `submit()` entry point."""

    def __init__(self, client: _AlpacaTradingClient | None = None) -> None:
        s = get_settings()
        if not s.ALPACA_PAPER_TRADE:
            raise RuntimeError("PaperBroker requires ALPACA_PAPER_TRADE=True")
        if s.LIVE_TRADING == "1":
            # Defense in depth: even though the alpaca client is constructed
            # with paper=True below, refusing here keeps the env-level kill
            # switch consistent with `scripts/place_order.py` and the
            # `dashboard/api/kill.py` guard.
            raise RuntimeError(
                "PaperBroker refuses to construct with LIVE_TRADING=1; v1 is paper-only"
            )

        if client is not None:
            self._client = client
            return

        from alpaca.trading.client import TradingClient

        if not s.ALPACA_API_KEY or not s.ALPACA_SECRET_KEY:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY missing — set them in .env")
        self._client = TradingClient(
            s.ALPACA_API_KEY,
            s.ALPACA_SECRET_KEY,
            paper=True,
        )

    def submit(self, order: Order, token: ApprovalToken) -> Submission:
        """Submit an order. Requires both-gate approval token."""
        if not token.risk_reason or not token.compliance_reason:
            raise PermissionError("ApprovalToken missing gate reason")

        request = self._build_request(order)
        result = self._client.submit_order(order_data=request)

        return Submission(
            broker_order_id=str(getattr(result, "id", "")),
            client_order_id=order.client_order_id,
            accepted_at=datetime.now(UTC),
            status=str(getattr(result, "status", "accepted")),
        )

    @staticmethod
    def _build_request(order: Order):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL
        tif = {
            "day": TimeInForce.DAY,
            "gtc": TimeInForce.GTC,
            "ioc": TimeInForce.IOC,
            "fok": TimeInForce.FOK,
        }[order.time_in_force]

        kwargs = {
            "symbol": order.symbol,
            "qty": order.qty,
            "side": side,
            "time_in_force": tif,
            "client_order_id": order.client_order_id,
            "extended_hours": order.extended_hours,
        }
        if order.order_type == "market":
            return MarketOrderRequest(**kwargs)
        kwargs["limit_price"] = float(order.limit_price)
        return LimitOrderRequest(**kwargs)


class LiveBroker:
    """Stub. v1 does not support live trading. See docs/policy.md."""

    def submit(self, *args, **kwargs) -> NoReturn:
        raise NotImplementedError(
            "live trading is disabled in v1; see docs/policy.md "
            "(re-enabling requires coordinated changes to docs/policy.md "
            "and src/execution/broker.py)"
        )
