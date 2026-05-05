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
    with pytest.warns(UserWarning, match="funding rate fetch failed"):
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
