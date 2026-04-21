"""PaperBroker + ApprovalToken with a fake alpaca client."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from src.config import get_settings
from src.execution.broker import (
    LiveBroker,
    PaperBroker,
    approval_token,
)
from src.execution.orders import Order
from src.risk.limits import Decision


class FakeAlpacaClient:
    def __init__(self):
        self.submitted = []

    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return SimpleNamespace(id="broker-123", status="accepted")

    def get_account(self):
        return SimpleNamespace(equity="100000")


def _approval(cycle_id="c1"):
    return approval_token(
        cycle_id=cycle_id,
        risk=Decision(True, "qty=10 within all caps", adjusted_size=10),
        compliance=Decision(True, "all checks pass"),
    )


def _order(**kw):
    defaults = dict(
        client_order_id="01H_TEST_TEST_TEST_TEST_AB",
        symbol="SPY",
        qty=10,
        side="buy",
        order_type="limit",
        time_in_force="day",
        limit_price=Decimal("100.50"),
    )
    defaults.update(kw)
    return Order(**defaults)


def test_approval_token_factory_requires_both_approvals():
    with pytest.raises(PermissionError):
        approval_token("c1", Decision(False, "reject"), Decision(True, "ok"))
    with pytest.raises(PermissionError):
        approval_token("c1", Decision(True, "ok"), Decision(False, "reject"))


def test_approval_token_requires_reasons():
    with pytest.raises(PermissionError):
        approval_token("c1", Decision(True, ""), Decision(True, "ok"))


def test_approval_token_holds_reasons():
    t = _approval()
    assert "caps" in t.risk_reason
    assert "checks" in t.compliance_reason


def test_paper_broker_submit_passes_through():
    fake = FakeAlpacaClient()
    broker = PaperBroker(client=fake)
    sub = broker.submit(_order(), _approval())
    assert sub.broker_order_id == "broker-123"
    assert sub.client_order_id.startswith("01H_TEST")
    assert len(fake.submitted) == 1


def test_paper_broker_idempotency_key_round_trips():
    fake = FakeAlpacaClient()
    broker = PaperBroker(client=fake)
    coid = "01H_UNIQUE_ID_FOR_TESTING_X"
    sub = broker.submit(_order(client_order_id=coid), _approval())
    assert sub.client_order_id == coid
    submitted = fake.submitted[0]
    assert getattr(submitted, "client_order_id", None) == coid


def test_paper_broker_rejects_when_paper_disabled(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "False")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        PaperBroker(client=FakeAlpacaClient())


def test_live_broker_always_raises():
    with pytest.raises(NotImplementedError) as exc:
        LiveBroker().submit("anything", "more")
    assert "policy.md" in str(exc.value)
