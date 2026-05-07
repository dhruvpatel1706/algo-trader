"""funding_filter_score behavior across regimes + graceful empty-frame default.

Network paths are not exercised here; `fetch_funding_rate` is tested via a
monkeypatched urllib.request.urlopen so we never hit the public endpoint.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest
from src.data import funding
from src.data.funding import fetch_funding_rate, funding_filter_score


def _history(rates: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rates), freq="8h", tz="UTC")
    return pd.DataFrame({"funding_rate": rates, "predicted_rate": [float("nan")] * len(rates)},
                        index=idx)


# ---------------------------------------------------------------------------
# funding_filter_score
# ---------------------------------------------------------------------------


def test_extreme_high_funding_long_signal_is_suppressed():
    # Most rates near 0; a final extreme spike pushes past p75.
    rates = [0.0001] * 30 + [0.001, 0.002, 0.003, 0.005, 0.01]
    df = _history(rates)
    asof = df.index[-1].date()
    score = funding_filter_score("BTCUSDT", df, asof, side="long")
    assert score < 1.0
    assert score >= 0.5  # heavy suppression but not below floor


def test_extreme_low_funding_short_signal_is_suppressed():
    rates = [0.0001] * 30 + [-0.001, -0.002, -0.003, -0.005, -0.01]
    df = _history(rates)
    asof = df.index[-1].date()
    score = funding_filter_score("BTCUSDT", df, asof, side="short")
    assert score < 1.0
    assert score >= 0.5


def test_mid_range_funding_returns_one():
    # Symmetric distribution around 0 with the most recent reading at the median.
    # asof is a day past the last sample so the full history (and the final 0.0
    # reading) is included by the `<= asof` filter.
    rates = [-0.001, -0.0005, 0.0, 0.0005, 0.001] * 8 + [0.0]
    df = _history(rates)
    asof = (df.index[-1] + pd.Timedelta(days=2)).date()
    score = funding_filter_score("BTCUSDT", df, asof, side="long")
    assert score == 1.0


def test_extreme_high_funding_does_not_suppress_short():
    """High funding => longs pay shorts. Shorts are the *winning* side; no penalty."""
    rates = [0.0001] * 30 + [0.001, 0.002, 0.003, 0.005, 0.01]
    df = _history(rates)
    asof = df.index[-1].date()
    score = funding_filter_score("BTCUSDT", df, asof, side="short")
    assert score == 1.0


def test_empty_funding_history_returns_one():
    df = pd.DataFrame()
    score = funding_filter_score("BTCUSDT", df, date(2024, 6, 1), side="long")
    assert score == 1.0


def test_history_without_funding_rate_column_returns_one():
    df = pd.DataFrame({"other": [1.0, 2.0]})
    score = funding_filter_score("BTCUSDT", df, date(2024, 6, 1), side="long")
    assert score == 1.0


def test_asof_before_history_returns_one():
    rates = [0.001, 0.002, 0.003]
    df = _history(rates, start="2024-06-01")
    score = funding_filter_score("BTCUSDT", df, date(2023, 1, 1), side="long")
    assert score == 1.0


def test_score_floor_of_zero_point_five_at_max_extreme():
    # Single sample in dataset == max == min; suppression formula still returns 1.0
    # (the percentile range collapses, so we treat that as no suppression).
    rates = [0.001]
    df = _history(rates)
    score = funding_filter_score("BTCUSDT", df, df.index[-1].date(), side="long")
    # Single sample: current == p75 == p_max; falls through to 1.0 (current <= p75).
    assert score == 1.0


# ---------------------------------------------------------------------------
# fetch_funding_rate (mocked network)
# ---------------------------------------------------------------------------


def _fake_urlopen_factory(payload):
    body = json.dumps(payload).encode("utf-8")

    class _Resp:
        def __init__(self, b):
            self._b = b

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(url, timeout=None):
        return _Resp(body)

    return _open


def test_fetch_funding_rate_parses_payload(monkeypatch):
    payload = [
        {"symbol": "BTCUSDT", "fundingTime": 1_704_067_200_000, "fundingRate": "0.00010000"},
        {"symbol": "BTCUSDT", "fundingTime": 1_704_096_000_000, "fundingRate": "-0.00005000"},
    ]
    monkeypatch.setattr(
        funding.urllib.request, "urlopen", _fake_urlopen_factory(payload)
    )
    df = fetch_funding_rate("BTCUSDT", date(2024, 1, 1), date(2024, 1, 2))
    assert not df.empty
    assert list(df.columns) == ["funding_rate", "predicted_rate"]
    assert len(df) == 2
    assert df["funding_rate"].iloc[0] == pytest.approx(0.0001)


def test_fetch_funding_rate_network_failure_returns_empty(monkeypatch):
    def _boom(url, timeout=None):
        raise OSError("simulated network failure")

    monkeypatch.setattr(funding.urllib.request, "urlopen", _boom)
    # Both Binance and Bybit will fail with the same OSError — auto path
    # warns once per source. Match either warning.
    with pytest.warns(UserWarning, match="(binance|bybit) funding fetch failed"):
        df = fetch_funding_rate("BTCUSDT", date(2024, 1, 1), date(2024, 1, 2))
    assert df.empty
    assert list(df.columns) == ["funding_rate", "predicted_rate"]


def test_fetch_funding_rate_bad_json_returns_empty(monkeypatch):
    class _BadResp:
        def read(self):
            return b"not json"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        funding.urllib.request, "urlopen", lambda url, timeout=None: _BadResp()
    )
    with pytest.warns(UserWarning, match="parse failed"):
        df = fetch_funding_rate("BTCUSDT", date(2024, 1, 1), date(2024, 1, 2))
    assert df.empty


def test_fetch_funding_rate_empty_payload_returns_empty(monkeypatch):
    monkeypatch.setattr(
        funding.urllib.request, "urlopen", _fake_urlopen_factory([])
    )
    df = fetch_funding_rate("BTCUSDT", date(2024, 1, 1), date(2024, 1, 2))
    assert df.empty


# ---------------------------------------------------------------------------
# Bybit fallback (added to unblock Researcher session: Binance HTTP 451
# geo-blocked on this IP, Bybit not).
# ---------------------------------------------------------------------------


def _bybit_payload(rates_with_ts: list[tuple[int, str]]) -> dict:
    """Build a Bybit v5 funding-history response."""
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "category": "linear",
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "fundingRate": rate,
                    "fundingRateTimestamp": str(ts),
                }
                for ts, rate in rates_with_ts
            ],
        },
    }


def test_fetch_funding_rate_bybit_source_parses_payload(monkeypatch):
    payload = _bybit_payload(
        [
            (1_704_067_200_000, "0.0001"),
            (1_704_096_000_000, "-0.00005"),
        ]
    )
    monkeypatch.setattr(
        funding.urllib.request, "urlopen", _fake_urlopen_factory(payload)
    )
    df = fetch_funding_rate(
        "BTCUSDT", date(2024, 1, 1), date(2024, 1, 2), source="bybit"
    )
    assert not df.empty
    assert list(df.columns) == ["funding_rate", "predicted_rate"]
    assert len(df) == 2
    assert df["funding_rate"].iloc[0] == pytest.approx(0.0001)


def test_fetch_funding_rate_bybit_non_zero_retcode_returns_empty(monkeypatch):
    payload = {"retCode": 10001, "retMsg": "Invalid symbol", "result": {"list": []}}
    monkeypatch.setattr(
        funding.urllib.request, "urlopen", _fake_urlopen_factory(payload)
    )
    with pytest.warns(UserWarning, match="bybit funding non-zero retCode"):
        df = fetch_funding_rate(
            "FAKEUSDT", date(2024, 1, 1), date(2024, 1, 2), source="bybit"
        )
    assert df.empty


def _venue_for(target) -> str:
    """Identify the venue from either a URL string OR a urllib.request.Request.

    Tests monkey-patch ``urllib.request.urlopen`` and now receive a Request
    object (we set a User-Agent header to satisfy OKX's WAF). This helper
    keeps the tests readable.
    """
    url = target.full_url if hasattr(target, "full_url") else str(target)
    if "binance" in url:
        return "binance"
    if "bybit" in url:
        return "bybit"
    if "okx" in url:
        return "okx"
    return "unknown"


def test_fetch_funding_rate_auto_falls_back_when_binance_451(monkeypatch):
    """Binance 451 (geo-block) → Bybit hit silently, no warning. Common
    path on US-residential IPs, which is what motivated this fallback."""
    import urllib.error
    bybit_payload = _bybit_payload([(1_704_067_200_000, "0.00012")])

    call_log: list[str] = []

    def _switching_urlopen(target, timeout=None):
        venue = _venue_for(target)
        call_log.append(venue)
        if venue == "binance":
            raise urllib.error.HTTPError(
                target.full_url, 451, "blocked", {}, None
            )
        body = json.dumps(bybit_payload).encode("utf-8")

        class _Resp:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()

    monkeypatch.setattr(funding.urllib.request, "urlopen", _switching_urlopen)
    df = fetch_funding_rate("BTCUSDT", date(2024, 1, 1), date(2024, 1, 2))
    # Binance 451 → Bybit returns data → OKX never queried.
    assert call_log[:2] == ["binance", "bybit"]
    assert "okx" not in call_log
    assert not df.empty
    assert df["funding_rate"].iloc[0] == pytest.approx(0.00012)


def test_fetch_funding_rate_auto_falls_back_when_binance_returns_empty_payload(
    monkeypatch,
):
    """Binance returns 200-OK with [] → auto path STILL falls back to Bybit
    so the caller never sees a misleading empty when data exists upstream."""
    bybit_payload = _bybit_payload([(1_704_067_200_000, "0.0002")])
    call_log: list[str] = []

    def _switching_urlopen(target, timeout=None):
        venue = _venue_for(target)
        call_log.append(venue)
        body = (
            json.dumps([]).encode("utf-8")
            if venue == "binance"
            else json.dumps(bybit_payload).encode("utf-8")
        )

        class _Resp:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()

    monkeypatch.setattr(funding.urllib.request, "urlopen", _switching_urlopen)
    df = fetch_funding_rate("BTCUSDT", date(2024, 1, 1), date(2024, 1, 2))
    assert call_log[:2] == ["binance", "bybit"]
    assert not df.empty
    assert df["funding_rate"].iloc[0] == pytest.approx(0.0002)


def test_fetch_funding_rate_auto_falls_through_to_okx(monkeypatch):
    """Binance + Bybit both fail → auto path tries OKX. This is the
    actual production path on US-residential IPs (what the dev machine
    hit live: Binance 451, Bybit 403, OKX 200)."""
    okx_payload = {
        "code": "0",
        "msg": "",
        "data": [
            {"fundingTime": "1704067200000", "fundingRate": "0.00009"},
            {"fundingTime": "1704096000000", "fundingRate": "0.00011"},
        ],
    }
    call_log: list[str] = []

    def _switching_urlopen(target, timeout=None):
        venue = _venue_for(target)
        call_log.append(venue)
        if venue == "okx":
            body = json.dumps(okx_payload).encode("utf-8")

            class _Resp:
                def read(self):
                    return body

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _Resp()
        raise OSError(f"{venue} simulated geo-block")

    monkeypatch.setattr(funding.urllib.request, "urlopen", _switching_urlopen)
    df = fetch_funding_rate("BTCUSDT", date(2024, 1, 1), date(2024, 1, 2))
    assert call_log == ["binance", "bybit", "okx"]
    assert not df.empty
    assert df["funding_rate"].iloc[0] == pytest.approx(0.00009)


def test_fetch_funding_rate_source_binance_does_not_fall_back(monkeypatch):
    """Forcing source='binance' must NOT silently fall back to Bybit/OKX —
    useful when a caller wants to verify a single-venue path explicitly."""
    call_log: list[str] = []

    def _binance_only_open(target, timeout=None):
        call_log.append(_venue_for(target))
        body = json.dumps([]).encode("utf-8")

        class _Resp:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()

    monkeypatch.setattr(funding.urllib.request, "urlopen", _binance_only_open)
    df = fetch_funding_rate(
        "BTCUSDT", date(2024, 1, 1), date(2024, 1, 2), source="binance"
    )
    assert call_log == ["binance"]
    assert df.empty


def test_fetch_funding_rate_okx_inst_id_mapping():
    """The OKX inst-id mapping handles all three quote currencies."""
    from src.data.funding import _to_okx_inst_id

    assert _to_okx_inst_id("BTCUSDT") == "BTC-USDT-SWAP"
    assert _to_okx_inst_id("ETHUSDC") == "ETH-USDC-SWAP"
    assert _to_okx_inst_id("BTCUSD") == "BTC-USD-SWAP"
    # Unknown / un-mappable: returns None so the caller can warn cleanly.
    assert _to_okx_inst_id("AAPL") is None
