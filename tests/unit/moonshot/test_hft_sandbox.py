from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import pytest
from src.moonshot import hft_sandbox
from src.moonshot.hft_sandbox import HftFill, HftSandbox


@dataclass
class _Order:
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    price: float
    ts: datetime


@pytest.fixture
def book() -> dict:
    return {"displayed_liquidity": 10_000, "vol": 0.005}


@pytest.fixture
def sandbox() -> HftSandbox:
    return HftSandbox(latency_budget_us=2000, seed=42)


def test_submit_produces_fill_with_measured_latency(sandbox: HftSandbox, book: dict) -> None:
    order = _Order(
        symbol="AAPL",
        side="buy",
        qty=100,
        price=150.0,
        ts=datetime(2026, 5, 4, 14, 0, tzinfo=UTC),
    )
    fill = sandbox.submit(order, book)
    assert isinstance(fill, HftFill)
    assert fill.symbol == "AAPL"
    assert fill.side == "buy"
    assert fill.qty == 100
    assert fill.intended_price == 150.0
    # Buy slippage should push the simulated price up slightly.
    assert fill.simulated_price >= 150.0
    # Latency is positive and capped at 10x the budget.
    assert fill.latency_us > 0
    assert fill.latency_us <= 2000 * 10
    # Queue position must be in [0, 1].
    assert 0.0 <= fill.queue_position_estimate <= 1.0


def test_sell_slippage_is_negative(sandbox: HftSandbox, book: dict) -> None:
    order = _Order(
        symbol="AAPL",
        side="sell",
        qty=100,
        price=150.0,
        ts=datetime(2026, 5, 4, tzinfo=UTC),
    )
    fill = sandbox.submit(order, book)
    # Sell slippage moves price the wrong way for the seller (down).
    assert fill.simulated_price <= 150.0


def test_stats_returns_reasonable_percentiles(book: dict) -> None:
    sandbox = HftSandbox(latency_budget_us=1000, seed=7)
    for i in range(200):
        order = _Order(
            symbol="AAPL" if i % 2 == 0 else "MSFT",
            side="buy",
            qty=10,
            price=100.0 + i * 0.01,
            ts=datetime(2026, 5, 4, tzinfo=UTC),
        )
        sandbox.submit(order, book)
    stats = sandbox.stats()
    assert stats["count"] == 200
    assert stats["latency_p50_us"] <= stats["latency_p95_us"] <= stats["latency_p99_us"]
    assert stats["latency_p99_us"] <= 1000 * 10  # cap
    assert stats["fill_ratio"] == 1.0
    assert "AAPL" in stats["slippage_bps_by_symbol"]
    assert "MSFT" in stats["slippage_bps_by_symbol"]


def test_stats_empty() -> None:
    sandbox = HftSandbox()
    stats = sandbox.stats()
    assert stats["count"] == 0
    assert stats["latency_p99_us"] == 0


def test_sandbox_never_references_real_broker() -> None:
    """SAFETY: HftSandbox must NEVER bridge to a real broker."""
    # 1) Explicit safety flag is False and stays False.
    assert HftSandbox.LIVE_BROKER_BRIDGE is False
    s = HftSandbox()
    assert s.LIVE_BROKER_BRIDGE is False

    # 2) The module source must not import from src.execution (the real broker
    #    surface) and must not reference alpaca clients.
    src = inspect.getsource(hft_sandbox)
    forbidden = ["src.execution", "alpaca", "AlpacaClient", "TradingClient"]
    for token in forbidden:
        assert token not in src, f"HftSandbox must not reference {token!r}"
