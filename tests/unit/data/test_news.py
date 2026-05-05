"""Unit tests for src.data.news.

Network calls are stubbed via httpx.Client monkeypatch. No real HTTP.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx as _httpx
import pytest
from src.data import news as news_mod
from src.data.news import NewsArticle, _hash_body, _make_id, fetch_finnhub_news


def test_fetch_finnhub_news_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    out = fetch_finnhub_news(["AAPL"], since=datetime(2024, 1, 1, tzinfo=UTC))
    assert out == []


def test_fetch_finnhub_news_explicit_empty_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    out = fetch_finnhub_news(
        ["AAPL"],
        since=datetime(2024, 1, 1, tzinfo=UTC),
        api_key="",
    )
    assert out == []


def test_body_hash_is_stable_for_same_body():
    a = _hash_body("Apple beats earnings")
    b = _hash_body("Apple beats earnings")
    assert a == b
    assert a != _hash_body("Apple misses earnings")


def test_news_article_id_is_stable_across_calls():
    body = "Same body, twice."
    body_hash = _hash_body(body)
    id1 = _make_id("finnhub", "AAPL", "https://example.com/1", body_hash)
    id2 = _make_id("finnhub", "AAPL", "https://example.com/1", body_hash)
    assert id1 == id2
    # Different URL -> different ID.
    id3 = _make_id("finnhub", "AAPL", "https://example.com/2", body_hash)
    assert id1 != id3


def test_news_article_dedup_via_body_hash():
    a = NewsArticle(
        id="x",
        source="finnhub",
        ticker="AAPL",
        headline="h",
        body="same",
        url="u",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        body_hash=_hash_body("same"),
    )
    b = NewsArticle(
        id="y",
        source="finnhub",
        ticker="AAPL",
        headline="h2",
        body="same",
        url="u2",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        body_hash=_hash_body("same"),
    )
    assert a.body_hash == b.body_hash


def test_fetch_finnhub_news_parses_response(monkeypatch):
    """Mock the httpx client; verify article fields and stable IDs."""

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    captured_params: list[dict] = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            captured_params.append(params)
            return _Resp(
                [
                    {
                        "headline": "AAPL beats expectations",
                        "summary": "Strong quarter for the iPhone maker.",
                        "url": "https://example.com/aapl-beats",
                        "datetime": 1_700_000_000,
                    },
                    {
                        # missing headline -> dropped
                        "summary": "no headline",
                        "url": "https://example.com/x",
                        "datetime": 1_700_000_001,
                    },
                ]
            )

    monkeypatch.setattr(news_mod.httpx, "Client", _Client)
    # Avoid the rate-limit sleep in tests.
    monkeypatch.setattr(news_mod, "_RATE_LIMIT_SLEEP_SEC", 0.0)
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")

    out = fetch_finnhub_news(
        ["AAPL"],
        since=datetime(2024, 1, 1, tzinfo=UTC),
    )

    assert len(out) == 1
    art = out[0]
    assert art.ticker == "AAPL"
    assert art.source == "finnhub"
    assert art.headline == "AAPL beats expectations"
    assert art.body == "Strong quarter for the iPhone maker."
    assert art.url == "https://example.com/aapl-beats"
    assert isinstance(art.published_at, datetime)

    # ID is deterministic: re-fetch with the same payload yields the same ID.
    out2 = fetch_finnhub_news(
        ["AAPL"],
        since=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert out2[0].id == art.id

    # The token + symbol made it through as params.
    assert captured_params[0]["symbol"] == "AAPL"
    assert captured_params[0]["token"] == "test_key"


def test_fetch_finnhub_news_handles_http_error_gracefully(monkeypatch):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            raise _httpx.ConnectError("boom")

    monkeypatch.setattr(news_mod.httpx, "Client", _Client)
    monkeypatch.setattr(news_mod, "_RATE_LIMIT_SLEEP_SEC", 0.0)
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")

    out = fetch_finnhub_news(
        ["AAPL", "MSFT"],
        since=datetime(2024, 1, 1, tzinfo=UTC),
    )
    # No crash; just no articles.
    assert out == []


def test_fetch_finnhub_news_sleeps_between_tickers(monkeypatch):
    """One sleep between successive tickers; none before the first."""

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            return _Resp()

    sleeps: list[float] = []
    monkeypatch.setattr(news_mod.time, "sleep", sleeps.append)
    monkeypatch.setattr(news_mod.httpx, "Client", _Client)
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")

    fetch_finnhub_news(
        ["AAPL", "MSFT", "GOOGL"],
        since=datetime(2024, 1, 1, tzinfo=UTC),
    )
    # 3 tickers -> 2 inter-ticker sleeps.
    assert len(sleeps) == 2


@pytest.mark.parametrize("api_key_via", ["env", "argument"])
def test_fetch_finnhub_news_accepts_key_from_env_or_arg(monkeypatch, api_key_via):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    captured = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            captured["params"] = params
            return _Resp()

    monkeypatch.setattr(news_mod.httpx, "Client", _Client)
    monkeypatch.setattr(news_mod, "_RATE_LIMIT_SLEEP_SEC", 0.0)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)

    if api_key_via == "env":
        monkeypatch.setenv("FINNHUB_API_KEY", "from_env")
        fetch_finnhub_news(["AAPL"], since=datetime(2024, 1, 1, tzinfo=UTC))
        assert captured["params"]["token"] == "from_env"
    else:
        fetch_finnhub_news(
            ["AAPL"],
            since=datetime(2024, 1, 1, tzinfo=UTC),
            api_key="from_arg",
        )
        assert captured["params"]["token"] == "from_arg"
