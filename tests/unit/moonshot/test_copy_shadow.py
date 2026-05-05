from __future__ import annotations

import inspect
import math
from datetime import UTC, datetime, timedelta

import pytest
from src.moonshot import copy_shadow
from src.moonshot.copy_shadow import ShadowTrade, compare_pnl, simulate_our_fill


def _make_trade(
    ticker: str = "AAPL",
    side: str = "buy",
    source_price: float = 100.0,
    source_qty: int = 1000,
    source_ts: datetime | None = None,
) -> ShadowTrade:
    return ShadowTrade(
        source_id="rep_x",
        source_label="Rep. X (D-CA)",
        ticker=ticker,
        side=side,  # type: ignore[arg-type]
        source_qty=source_qty,
        source_price=source_price,
        source_ts=source_ts or datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_simulate_our_fill_majors_25bps_buy() -> None:
    trade = _make_trade(ticker="AAPL", side="buy", source_price=100.0)
    out = simulate_our_fill(trade, our_capital=10_000.0)
    # 25 bps slippage on a buy => price 0.25% higher.
    assert math.isclose(out.our_simulated_price, 100.0 * (1 + 25 / 10_000.0), rel_tol=1e-9)
    assert out.our_simulated_qty == int(10_000.0 // out.our_simulated_price)


def test_simulate_our_fill_non_major_100bps_sell() -> None:
    trade = _make_trade(ticker="ABCD", side="sell", source_price=50.0, source_qty=500)
    out = simulate_our_fill(trade, our_capital=100_000.0)
    # 100 bps on a sell => price 1% lower.
    assert math.isclose(out.our_simulated_price, 50.0 * (1 - 100 / 10_000.0), rel_tol=1e-9)
    # Sell mirrors source qty when we have plenty of capital.
    assert out.our_simulated_qty == 500


def test_simulate_our_fill_with_book_callback() -> None:
    trade = _make_trade(ticker="AAPL", side="buy", source_price=100.0)

    def book_cb(_ticker: str, _ts: datetime) -> dict:
        return {"slippage_bps": 5.0, "spread_bps": 2.0}

    out = simulate_our_fill(trade, our_capital=10_000.0, book_depth_callback=book_cb)
    assert math.isclose(out.our_simulated_price, 100.0 * (1 + 7 / 10_000.0), rel_tol=1e-9)


def test_compare_pnl_aggregates_and_ratio() -> None:
    base_ts = datetime(2026, 1, 1, tzinfo=UTC)
    trades = []
    for _ in range(10):
        t = _make_trade(source_price=100.0, source_qty=100, source_ts=base_ts)
        trades.append(simulate_our_fill(t, our_capital=10_000.0))

    # Pricer: every name 30 days later trades at $110.
    def pricer(_ticker: str, _ts: datetime) -> float:
        return 110.0

    result = compare_pnl(trades, horizon_days=30, asof_pricer=pricer)
    assert result["count"] == 10
    assert result["horizon_days"] == 30
    # Source bought at 100, sold at 110 => $10/sh * 100 sh * 10 trades = $10,000.
    assert math.isclose(result["source_total_pnl"], 10_000.0, rel_tol=1e-6)
    # Our entries are slightly worse, so our P&L should be slightly less.
    assert result["our_total_pnl"] < result["source_total_pnl"]
    assert 0.0 < result["ratio"] < 1.0
    # Acceptance requires >= 50 trades; with 10 we cannot accept.
    assert result["accepted"] is False


def test_compare_pnl_acceptance_with_50_trades() -> None:
    base_ts = datetime(2026, 1, 1, tzinfo=UTC)
    trades = []
    for _ in range(60):
        t = _make_trade(source_price=100.0, source_qty=100, source_ts=base_ts)
        trades.append(simulate_our_fill(t, our_capital=10_000.0))

    def pricer(_ticker: str, _ts: datetime) -> float:
        return 110.0

    result = compare_pnl(trades, horizon_days=30, asof_pricer=pricer)
    # Slippage is 25 bps = 0.25%, ratio close to 0.975 -> well above 0.6.
    assert result["accepted"] is True


def test_compare_pnl_invalid_horizon_raises() -> None:
    with pytest.raises(ValueError):
        compare_pnl([], horizon_days=-1, asof_pricer=lambda *_: 100.0)


def test_compare_pnl_skips_failing_pricer() -> None:
    base_ts = datetime(2026, 1, 1, tzinfo=UTC)
    t = _make_trade(source_price=100.0, source_qty=100, source_ts=base_ts)
    out = simulate_our_fill(t, our_capital=10_000.0)

    def bad_pricer(_ticker: str, _ts: datetime) -> float:
        raise RuntimeError("no data")

    result = compare_pnl([out], horizon_days=30, asof_pricer=bad_pricer)
    assert result["count"] == 0


def test_horizon_uses_correct_exit_ts() -> None:
    # Verify that compare_pnl actually passes source_ts + horizon_days to the pricer.
    seen: list[datetime] = []

    def pricer(_ticker: str, ts: datetime) -> float:
        seen.append(ts)
        return 100.0

    base_ts = datetime(2026, 1, 1, tzinfo=UTC)
    t = _make_trade(source_ts=base_ts)
    out = simulate_our_fill(t, our_capital=100_000.0)
    compare_pnl([out], horizon_days=90, asof_pricer=pricer)
    assert seen == [base_ts + timedelta(days=90)]


def test_paper_only_safety() -> None:
    assert copy_shadow.LIVE_BROKER_BRIDGE is False
    src = inspect.getsource(copy_shadow)
    for token in ["src.execution", "alpaca", "TradingClient"]:
        assert token not in src
