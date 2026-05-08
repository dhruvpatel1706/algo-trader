"""Data-loader correctness tests.

The NVDA split test is a network-touching regression for the auto_adjust bug.
Marked `network`; skips cleanly if yfinance is unreachable or rate-limited.

The Polygon Stocks tests are pure-mock — they patch ``safe_urlopen`` so the
test never hits the network. They prove the leg slots into the chain
correctly (cache → Alpaca → Polygon → yfinance), parses the actual
Polygon JSON shape, and falls through cleanly when the API returns
empty or errors.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from io import BytesIO
from unittest.mock import patch

import pandas as pd
import pytest
from src.data import loader
from src.data.loader import load_daily_bars


def _bars(start: str, periods: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000,
        },
        index=idx,
    )


def test_loader_refetches_when_cache_does_not_cover_requested_range(monkeypatch, tmp_path):
    monkeypatch.setattr(loader, "_CACHE_DIR", tmp_path)
    (tmp_path / "SPY_1d.parquet").write_bytes(b"")
    partial = _bars("2022-01-03", 5)
    partial.to_parquet(tmp_path / "SPY_1d.parquet")

    fetched = _bars("2020-01-02", 20)
    calls = {"count": 0}

    def fake_fetch(symbol, start, end):
        calls["count"] += 1
        assert symbol == "SPY"
        return fetched

    monkeypatch.setattr(loader, "_fetch_alpaca", fake_fetch)
    out = load_daily_bars(
        ("SPY",),
        date(2020, 1, 2),
        date(2020, 1, 29),
        use_cache=True,
        fallback_to_yfinance=False,
    )

    assert calls["count"] == 1
    assert len(out["SPY"]) == 20


@pytest.mark.network
def test_nvda_2024_06_split_is_adjusted_in_loader(monkeypatch):
    """NVDA had a 10:1 split effective 2024-06-10. With auto_adjust=True the
    historical close on 2024-06-07 is divided by 10 (split-adjusted backward),
    so the close ratio close[06-10] / close[06-07] should be ~1.0 (a normal
    daily move) instead of ~0.10 (which is what the unadjusted series prints).

    This is the regression test for the bug where auto_adjust=False let a
    fake -90% gap trigger ATR stops on every affected day.
    """
    # Force the loader to use yfinance directly. The autouse fixture sets dummy
    # Alpaca creds; with non-empty creds the loader hits Alpaca first and the
    # 401 is uncaught. Empty creds short-circuit straight to yfinance.
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "")
    from src.config import get_settings

    get_settings.cache_clear()

    bars = load_daily_bars(
        ("NVDA",),
        date(2024, 6, 1),
        date(2024, 6, 30),
        use_cache=False,
        fallback_to_yfinance=True,
    )
    if "NVDA" not in bars or bars["NVDA"].empty:
        pytest.skip("yfinance returned no data for NVDA (network or rate-limit)")

    df = bars["NVDA"].copy()
    # Index can be tz-aware datetimes; compare by calendar date.
    df.index = [ts.date() if hasattr(ts, "date") else ts for ts in df.index]

    if date(2024, 6, 7) not in df.index or date(2024, 6, 10) not in df.index:
        pytest.skip("NVDA bars missing 2024-06-07 or 2024-06-10 in returned range")

    close_07 = float(df.loc[date(2024, 6, 7), "close"])
    close_10 = float(df.loc[date(2024, 6, 10), "close"])
    ratio = close_10 / close_07

    # Adjusted: ratio should be near 1.0 (within a normal daily move).
    # Unadjusted (the bug): ratio ~ 0.10 because of the 10:1 split.
    assert 0.85 < ratio < 1.15, (
        f"NVDA close ratio 06-10/06-07 = {ratio:.4f}; expected ~1.0 (continuous adjusted "
        f"series). Got something split-shaped — is auto_adjust=True in src/data/loader.py?"
    )


# ---------------------------------------------------------------------------
# Polygon Stocks fetcher — provider-rotation chain tests (pure mock, no net)
# ---------------------------------------------------------------------------


def _polygon_response_payload(start_date: date, periods: int) -> dict:
    """Build the JSON shape Polygon returns from /v2/aggs/ticker/.../range/1/day."""
    base_ts_ms = int(pd.Timestamp(start_date, tz="UTC").timestamp() * 1000)
    one_day_ms = 86_400_000
    results = [
        {
            "t": base_ts_ms + i * one_day_ms,
            "o": 100.0 + i,
            "h": 101.5 + i,
            "l": 99.0 + i,
            "c": 100.5 + i,
            "v": 1_000_000 + i * 10,
        }
        for i in range(periods)
    ]
    return {
        "ticker": "TEST",
        "queryCount": periods,
        "resultsCount": periods,
        "adjusted": True,
        "results": results,
        "status": "OK",
    }


@contextmanager
def _mock_polygon_response(payload: dict | None):
    """Patch safe_urlopen to return ``payload`` (or an HTTP error if None)."""
    if payload is None:
        def _raise(*args, **kwargs):
            raise loader.urllib.error.HTTPError(
                "https://api.polygon.io/", 500, "boom", {}, BytesIO(b""),
            )
        with patch.object(loader, "safe_urlopen", side_effect=_raise) as m:
            yield m
        return

    body = json.dumps(payload).encode("utf-8")

    class _FakeCM:
        def __enter__(self_inner):  # noqa: N805 - context manager idiom
            return _FakeResp(body)

        def __exit__(self_inner, *exc):  # noqa: N805
            return False

    class _FakeResp:
        def __init__(self, b):
            self._b = b

        def read(self):
            return self._b

    def _open(url, **kwargs):
        return _FakeCM()

    with patch.object(loader, "safe_urlopen", side_effect=_open) as m:
        yield m


def test_polygon_fetcher_disabled_when_key_missing(monkeypatch):
    """No POLYGON_STOCKS_KEY -> _polygon_stocks_enabled() is False and the
    fetcher short-circuits to None without ever hitting the network."""
    monkeypatch.delenv("POLYGON_STOCKS_KEY", raising=False)
    assert not loader._polygon_stocks_enabled()
    out = loader._fetch_polygon_stocks("AAPL", date(2026, 1, 1), date(2026, 1, 5))
    assert out is None


def test_polygon_fetcher_parses_real_response_shape(monkeypatch):
    """When the key is set and the API returns the standard JSON, the
    fetcher emits a frame with the same shape as the Alpaca/yfinance legs:
    tz-aware UTC index, lowercase open/high/low/close/volume columns."""
    monkeypatch.setenv("POLYGON_STOCKS_KEY", "test-key")
    payload = _polygon_response_payload(date(2026, 1, 5), periods=5)
    with _mock_polygon_response(payload):
        df = loader._fetch_polygon_stocks("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    assert df is not None
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 5
    # Index is tz-aware UTC.
    assert df.index.tz is not None
    # Values match the synthetic payload (round-trip through dict + DF).
    assert float(df["close"].iloc[0]) == 100.5
    assert float(df["close"].iloc[-1]) == 104.5


def test_polygon_fetcher_returns_none_on_empty_results(monkeypatch):
    """Polygon returns ``{"results": []}`` for symbols/ranges with no
    coverage. Fetcher must return None so the loader falls through to
    yfinance instead of caching an empty frame."""
    monkeypatch.setenv("POLYGON_STOCKS_KEY", "test-key")
    with _mock_polygon_response({"results": [], "resultsCount": 0, "status": "OK"}):
        out = loader._fetch_polygon_stocks("ZZZZ", date(2026, 1, 5), date(2026, 1, 9))
    assert out is None


def test_polygon_fetcher_returns_none_on_http_error(monkeypatch):
    """Polygon 5xx / network failure must degrade silently to None."""
    monkeypatch.setenv("POLYGON_STOCKS_KEY", "test-key")
    with _mock_polygon_response(None):
        out = loader._fetch_polygon_stocks("AAPL", date(2026, 1, 5), date(2026, 1, 9))
    assert out is None


def test_load_daily_bars_uses_polygon_when_alpaca_returns_empty(
    monkeypatch, tmp_path
):
    """Provider chain test: Alpaca returns empty -> Polygon serves -> we
    skip yfinance entirely. Validates the full leg-routing in
    load_daily_bars rather than the raw fetcher."""
    monkeypatch.setattr(loader, "_CACHE_DIR", tmp_path)
    monkeypatch.setenv("POLYGON_STOCKS_KEY", "test-key")
    monkeypatch.setattr(loader, "_fetch_alpaca", lambda s, a, b: None)

    yfinance_calls = {"n": 0}

    def fake_yf(*args, **kwargs):
        yfinance_calls["n"] += 1
        return _bars("2026-01-05", 5)

    monkeypatch.setattr(loader, "_fetch_yfinance", fake_yf)

    payload = _polygon_response_payload(date(2026, 1, 5), periods=5)
    with _mock_polygon_response(payload):
        out = load_daily_bars(
            ("AAPL",),
            date(2026, 1, 5),
            date(2026, 1, 9),
            use_cache=False,
            fallback_to_yfinance=True,
        )
    assert "AAPL" in out
    assert len(out["AAPL"]) == 5
    assert yfinance_calls["n"] == 0, (
        "yfinance must NOT be called when Polygon returns valid data — "
        "it is the loud-warning leg of last resort"
    )


def test_load_daily_bars_falls_through_polygon_to_yfinance(monkeypatch, tmp_path):
    """When BOTH Alpaca and Polygon return empty, yfinance must still
    serve as the final fallback. Pinning this so that the polygon
    addition never accidentally shadows yfinance."""
    monkeypatch.setattr(loader, "_CACHE_DIR", tmp_path)
    monkeypatch.setenv("POLYGON_STOCKS_KEY", "test-key")
    monkeypatch.setattr(loader, "_fetch_alpaca", lambda s, a, b: None)
    monkeypatch.setattr(loader, "_fetch_polygon_stocks", lambda s, a, b: None)

    monkeypatch.setattr(loader, "_fetch_yfinance", lambda s, a, b: _bars("2026-01-05", 5))
    out = load_daily_bars(
        ("AAPL",),
        date(2026, 1, 5),
        date(2026, 1, 9),
        use_cache=False,
        fallback_to_yfinance=True,
    )
    assert "AAPL" in out
    assert len(out["AAPL"]) == 5
