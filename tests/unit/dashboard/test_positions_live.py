"""Tests for ``GET /api/positions/live``.

The endpoint blends two Alpaca surfaces:
1. Trading API ``get_all_positions()`` — gives qty, avg_entry, side. Its
   ``current_price`` field updates slowly on the paper crypto data tier
   (3+ minutes between ticks) which makes the dashboard look frozen.
2. Market-data ``CryptoLatestQuoteRequest`` — gives bid/ask that tick on
   Alpaca's actual rate. We use the mid as the live mark for crypto.

These tests exercise:
- Symbol detection (crypto vs equity) including both Alpaca shapes
  (``ETHUSD`` from trading API, ``ETH/USD`` from data API).
- Symbol normalization for cross-API lookup.
- Quote-mid override path: P&L is recomputed from the fresh mid, not
  copied from the stale snapshot.
- Fallback: if the data API returns nothing, we keep the snapshot price
  rather than zeroing out the row.
- The mark_as_of / mark_source fields surface the right value so the
  UI's freshness chip works.
"""

from __future__ import annotations

import pytest
from dashboard.api.multi_agent import (
    LivePosition,
    _is_crypto_symbol,
    _to_data_api_symbol,
    positions_live,
)

# ---------------------------------------------------------------------------
# Symbol classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("ETHUSD", True),
        ("BTCUSD", True),
        ("ETH/USD", True),
        ("BTC/USD", True),
        ("BTCUSDT", True),
        ("BTCUSDC", True),
        # Equities — must NOT be classified as crypto.
        ("AAPL", False),
        ("GOOGL", False),
        ("GLD", False),
        ("SPY", False),
        ("TLT", False),
        # 4-letter equity ending in 'USD' should still be crypto only by exact
        # length test (not currently a real ticker, but documents the boundary).
    ],
)
def test_is_crypto_symbol(symbol: str, expected: bool) -> None:
    assert _is_crypto_symbol(symbol) is expected


@pytest.mark.parametrize(
    "trading_form,data_form",
    [
        ("ETHUSD", "ETH/USD"),
        ("BTCUSD", "BTC/USD"),
        ("BTCUSDT", "BTC/USDT"),
        ("BTCUSDC", "BTC/USDC"),
        ("ETH/USD", "ETH/USD"),  # idempotent
        ("AAPL", "AAPL"),  # passthrough for equities
    ],
)
def test_to_data_api_symbol(trading_form: str, data_form: str) -> None:
    assert _to_data_api_symbol(trading_form) == data_form


# ---------------------------------------------------------------------------
# /api/positions/live behavior
# ---------------------------------------------------------------------------


class _FakeBroker:
    def __init__(self, positions, marks=None):
        self.positions = positions
        self.marks = marks or {}
        self.crypto_marks_calls: list[list[str]] = []

    def get_positions(self):
        return self.positions

    def get_crypto_marks(self, symbols):
        self.crypto_marks_calls.append(list(symbols))
        return self.marks


@pytest.fixture
def patch_broker(monkeypatch):
    """Replace get_broker_proxy with a controllable fake."""

    def _patch(broker):

        # The endpoint imports get_broker_proxy lazily inside the function,
        # so patch the module the import points at.
        from dashboard.api import broker_proxy as bp

        monkeypatch.setattr(bp, "get_broker_proxy", lambda: broker)
        return broker

    return _patch


@pytest.mark.asyncio
async def test_crypto_position_uses_latest_quote_mid(patch_broker) -> None:
    """For a crypto position, current_price is the mid of (bid, ask), not
    the slow-ticking position snapshot."""
    broker = patch_broker(
        _FakeBroker(
            positions=[
                {
                    "symbol": "ETHUSD",
                    "qty": 3.99,
                    "avg_entry_price": 2348.70,
                    "current_price": 2349.60,  # stale — will be overridden
                    "unrealized_pl": 3.59,
                    "unrealized_plpc": 0.000383,
                    "side": "long",
                }
            ],
            marks={
                "ETH/USD": {
                    "bid": 2350.0,
                    "ask": 2354.0,
                    "mid": 2352.0,
                    "ts": "2026-05-06T21:36:22+00:00",
                }
            },
        )
    )

    out = await positions_live()
    assert len(out) == 1
    p = out[0]
    assert p.symbol == "ETHUSD"
    assert p.qty == pytest.approx(3.99)
    assert p.current_price == pytest.approx(2352.0)
    assert p.mark_source == "latest_quote_mid"
    assert p.mark_as_of == "2026-05-06T21:36:22+00:00"
    # P&L recomputed from live mid: (2352 - 2348.70) * 3.99 = 13.167
    assert p.unrealized_pl == pytest.approx((2352.0 - 2348.70) * 3.99)
    # Conversion was passed in data-API form to the broker.
    assert broker.crypto_marks_calls == [["ETH/USD"]]


@pytest.mark.asyncio
async def test_equity_position_uses_snapshot_price(patch_broker) -> None:
    """For an equity, no data-API call is needed — the snapshot is fine."""
    broker = patch_broker(
        _FakeBroker(
            positions=[
                {
                    "symbol": "SPY",
                    "qty": 10.0,
                    "avg_entry_price": 500.0,
                    "current_price": 510.0,
                    "unrealized_pl": 100.0,
                    "unrealized_plpc": 0.02,
                    "side": "long",
                }
            ],
            marks={},
        )
    )

    out = await positions_live()
    assert len(out) == 1
    p = out[0]
    assert p.symbol == "SPY"
    assert p.current_price == pytest.approx(510.0)
    assert p.mark_source == "position_snapshot"
    # No crypto-mark call was made for an equity-only universe.
    assert broker.crypto_marks_calls == []


@pytest.mark.asyncio
async def test_data_api_failure_falls_back_to_snapshot(patch_broker) -> None:
    """If get_crypto_marks raises, keep the snapshot price — never zero."""

    class _ExplodingBroker(_FakeBroker):
        def get_crypto_marks(self, symbols):
            raise RuntimeError("data api down")

    patch_broker(
        _ExplodingBroker(
            positions=[
                {
                    "symbol": "BTCUSD",
                    "qty": 0.5,
                    "avg_entry_price": 80000.0,
                    "current_price": 81000.0,
                    "unrealized_pl": 500.0,
                    "unrealized_plpc": 0.0125,
                    "side": "long",
                }
            ],
        )
    )

    out = await positions_live()
    assert len(out) == 1
    p = out[0]
    assert p.current_price == pytest.approx(81000.0)
    assert p.mark_source == "position_snapshot"


@pytest.mark.asyncio
async def test_zero_mid_falls_back_to_snapshot(patch_broker) -> None:
    """Thin-book quote (mid == 0) doesn't override — keep the snapshot."""
    patch_broker(
        _FakeBroker(
            positions=[
                {
                    "symbol": "ETHUSD",
                    "qty": 1.0,
                    "avg_entry_price": 2300.0,
                    "current_price": 2350.0,
                    "unrealized_pl": 50.0,
                    "unrealized_plpc": 0.0217,
                    "side": "long",
                }
            ],
            marks={
                "ETH/USD": {
                    "bid": 0.0,
                    "ask": 0.0,
                    "mid": 0.0,
                    "ts": "2026-05-06T21:36:22+00:00",
                }
            },
        )
    )

    out = await positions_live()
    assert out[0].current_price == pytest.approx(2350.0)
    assert out[0].mark_source == "position_snapshot"


@pytest.mark.asyncio
async def test_mark_as_of_is_populated(patch_broker) -> None:
    """Even snapshot-source rows get a mark_as_of timestamp."""
    patch_broker(
        _FakeBroker(
            positions=[
                {
                    "symbol": "AAPL",
                    "qty": 5.0,
                    "avg_entry_price": 200.0,
                    "current_price": 205.0,
                    "unrealized_pl": 25.0,
                    "unrealized_plpc": 0.025,
                    "side": "long",
                }
            ],
        )
    )

    out = await positions_live()
    assert isinstance(out[0], LivePosition)
    assert out[0].mark_as_of is not None
    assert "T" in out[0].mark_as_of  # ISO8601 has a 'T' separator


@pytest.mark.asyncio
async def test_qty_stays_float_for_crypto(patch_broker) -> None:
    """Regression guard: fractional crypto qty must NOT be truncated."""
    patch_broker(
        _FakeBroker(
            positions=[
                {
                    "symbol": "ETHUSD",
                    "qty": 3.99,
                    "avg_entry_price": 2348.70,
                    "current_price": 2350.0,
                    "unrealized_pl": 5.187,
                    "unrealized_plpc": 0.0006,
                    "side": "long",
                }
            ],
        )
    )

    out = await positions_live()
    assert out[0].qty == pytest.approx(3.99)
    # JSON encoding (the wire format) must preserve the float, too.
    assert out[0].model_dump()["qty"] == pytest.approx(3.99)
