"""Unit tests for src.data.news_research.

No real network calls. ``httpx.Client`` is monkeypatched and the Anthropic SDK
stub path is exercised by deleting ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from src.data import news_research as nr_mod
from src.data.news import NewsArticle, _hash_body, _make_id
from src.data.news_research import (
    AssetNewsDigest,
    _keyword_match,
    _whole_word_present,
    build_digest,
    fetch_asset_news,
)
from src.data.sentiment import SentimentScore

# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def test_fetch_asset_news_no_finnhub_key_returns_empty(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    out = fetch_asset_news("gold")
    assert out == []


def test_fetch_asset_news_equity_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    out = fetch_asset_news("equity")
    assert out == []


# ---------------------------------------------------------------------------
# build_digest defensive paths
# ---------------------------------------------------------------------------


def test_build_digest_no_api_keys_returns_empty_neutral(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    digest = build_digest("gold")
    assert isinstance(digest, AssetNewsDigest)
    assert digest.asset_class == "gold"
    assert digest.universe_key == "gold"
    assert digest.n_articles == 0
    assert digest.rolling_24h_score == 0.0
    assert digest.top_articles == []
    assert isinstance(digest.generated_at, datetime)


def test_build_digest_equity_resolves_default_universe_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    digest = build_digest("equity")
    assert digest.asset_class == "equity"
    assert digest.universe_key == "large_caps_50"


def test_asset_news_digest_dataclass_round_trips():
    art = NewsArticle(
        id="x" * 32,
        source="finnhub",
        ticker="GOLD",
        headline="Gold hits new high",
        body="Bullion rallied...",
        url="https://example.com/g",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        body_hash=_hash_body("Bullion rallied..."),
    )
    score = SentimentScore(
        article_id=art.id,
        score=0.4,
        label="bullish",
        confidence=0.8,
        model="stub",
        scored_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    digest = AssetNewsDigest(
        asset_class="gold",
        universe_key="gold",
        n_articles=1,
        rolling_24h_score=0.32,
        top_articles=[(art, score)],
        generated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    # Frozen + slots -> attribute set should fail.
    assert digest.asset_class == "gold"
    assert digest.universe_key == "gold"
    assert digest.n_articles == 1
    assert digest.rolling_24h_score == 0.32
    assert digest.top_articles[0][0].headline == "Gold hits new high"


# ---------------------------------------------------------------------------
# Keyword filter
# ---------------------------------------------------------------------------


def test_keyword_match_includes_gold_price_high():
    assert _keyword_match("gold", "Gold price hits new high", "Bullion rallied as Fed pauses.")


def test_keyword_match_excludes_gold_coast_real_estate():
    # "gold-coast" must not match. Negative-phrase scrubbing covers this.
    assert not _keyword_match("gold", "Gold-Coast real estate booms", "Property prices soar.")


def test_keyword_match_excludes_unrelated_text():
    assert not _keyword_match("gold", "Tesla beats earnings", "Strong quarter for EV maker.")


def test_keyword_match_silver_industrial():
    assert _keyword_match("silver", "Industrial silver demand surges", "Solar manufacturing up.")


def test_keyword_match_bonds_treasury_yield():
    assert _keyword_match("bonds", "10-year yield spikes", "Treasury auction sees weak demand.")


def test_keyword_match_crypto_bitcoin():
    assert _keyword_match("crypto", "Bitcoin tops $80k", "BTC rallied amid ETF inflows.")


def test_whole_word_does_not_match_substring():
    # "gold" as a substring of "marigold" must NOT trigger.
    assert not _whole_word_present("marigolds bloom in spring", "gold")


def test_whole_word_matches_with_punctuation():
    assert _whole_word_present("the price of gold!", "gold")


# ---------------------------------------------------------------------------
# Macro fetcher with mocked httpx
# ---------------------------------------------------------------------------


def _install_general_news_mock(monkeypatch, payload):
    class _Resp:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            return None

        def json(self):
            return self._p

    captured: list[dict] = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def close(self):
            return None

        def get(self, url, params=None):
            captured.append({"url": url, "params": params})
            return _Resp(payload)

    monkeypatch.setattr(nr_mod.httpx, "Client", _Client)
    return captured


def test_fetch_asset_news_gold_macro_filters_by_keyword(monkeypatch):
    now_ts = int(datetime.now(tz=UTC).timestamp())
    payload = [
        {
            "headline": "Gold price hits new high",
            "summary": "Bullion soared as Fed pauses.",
            "url": "https://example.com/gold-1",
            "datetime": now_ts,
        },
        {
            "headline": "Gold-Coast real estate booms",
            "summary": "Property prices soar in Australia.",
            "url": "https://example.com/realestate",
            "datetime": now_ts,
        },
        {
            "headline": "Tesla earnings beat",
            "summary": "Strong quarter for EV maker.",
            "url": "https://example.com/tsla",
            "datetime": now_ts,
        },
    ]
    captured = _install_general_news_mock(monkeypatch, payload)
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")

    out = fetch_asset_news("gold")
    assert len(out) == 1
    assert out[0].headline == "Gold price hits new high"
    # Hit the general endpoint with the right params.
    assert captured[0]["params"]["category"] == "general"
    assert captured[0]["params"]["token"] == "test_key"


def test_fetch_asset_news_macro_drops_old_articles(monkeypatch):
    cutoff_ts = int((datetime.now(tz=UTC) - timedelta(days=30)).timestamp())
    payload = [
        {
            "headline": "Gold price hits new high",
            "summary": "Bullion soared.",
            "url": "https://example.com/gold-1",
            "datetime": cutoff_ts,
        },
    ]
    _install_general_news_mock(monkeypatch, payload)
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")

    # Default `since` is 7 days back, so a 30-day-old article is excluded.
    out = fetch_asset_news("gold")
    assert out == []


def test_fetch_asset_news_macro_dedupes_same_body(monkeypatch):
    now_ts = int(datetime.now(tz=UTC).timestamp())
    payload = [
        {
            "headline": "Gold price hits new high",
            "summary": "Bullion soared on Fed pause.",
            "url": "https://example.com/g1",
            "datetime": now_ts,
        },
        {
            "headline": "Gold price hits new high",  # same body -> deduped
            "summary": "Bullion soared on Fed pause.",
            "url": "https://example.com/g2",
            "datetime": now_ts,
        },
    ]
    _install_general_news_mock(monkeypatch, payload)
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")

    out = fetch_asset_news("gold")
    assert len(out) == 1


def test_fetch_asset_news_macro_handles_http_error(monkeypatch):
    import httpx as _httpx

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def close(self):
            return None

        def get(self, url, params=None):
            raise _httpx.ConnectError("boom")

    monkeypatch.setattr(nr_mod.httpx, "Client", _Client)
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    assert fetch_asset_news("gold") == []


# ---------------------------------------------------------------------------
# build_digest with mocked Finnhub macro + neutral stub scoring
# ---------------------------------------------------------------------------


def test_build_digest_gold_with_mocked_news_no_anthropic(monkeypatch):
    now_ts = int(datetime.now(tz=UTC).timestamp())
    payload = [
        {
            "headline": "Gold price hits new high",
            "summary": "Bullion soared on Fed pause.",
            "url": "https://example.com/gold-1",
            "datetime": now_ts,
        },
    ]
    _install_general_news_mock(monkeypatch, payload)
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    digest = build_digest("gold", today=date.today())
    assert digest.n_articles == 1
    # Stub scoring -> rolling 24h average remains 0 (zero confidence weights).
    assert digest.rolling_24h_score == 0.0
    assert len(digest.top_articles) == 1
    assert digest.top_articles[0][1].model == "stub"


def test_build_digest_caps_top_articles_at_ten(monkeypatch):
    now_ts = int(datetime.now(tz=UTC).timestamp())
    payload = [
        {
            "headline": f"Gold story {i}",
            "summary": f"Bullion drama number {i}",
            "url": f"https://example.com/g/{i}",
            "datetime": now_ts - i,
        }
        for i in range(15)
    ]
    _install_general_news_mock(monkeypatch, payload)
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    digest = build_digest("gold")
    assert digest.n_articles == 15
    assert len(digest.top_articles) == 10
    # Newest-first ordering preserved.
    assert digest.top_articles[0][0].headline == "Gold story 0"


# ---------------------------------------------------------------------------
# Rolling 24h average aggregation
# ---------------------------------------------------------------------------


def test_rolling_24h_average_weighted_by_confidence():
    now = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    art_recent = NewsArticle(
        id=_make_id("test", "GOLD", "u1", _hash_body("a")),
        source="finnhub",
        ticker="GOLD",
        headline="recent",
        body="a",
        url="u1",
        published_at=now - timedelta(hours=1),
        body_hash=_hash_body("a"),
    )
    art_old = NewsArticle(
        id=_make_id("test", "GOLD", "u2", _hash_body("b")),
        source="finnhub",
        ticker="GOLD",
        headline="old",
        body="b",
        url="u2",
        published_at=now - timedelta(hours=48),  # outside the 24h window
        body_hash=_hash_body("b"),
    )
    s_recent = SentimentScore(
        article_id=art_recent.id,
        score=0.6,
        label="bullish",
        confidence=0.8,
        model="claude-haiku",
        scored_at=now,
    )
    s_old = SentimentScore(
        article_id=art_old.id,
        score=-1.0,
        label="bearish",
        confidence=1.0,
        model="claude-haiku",
        scored_at=now,
    )
    avg = nr_mod._rolling_24h_average(
        [(art_recent, s_recent), (art_old, s_old)],
        now=now,
    )
    # Old article excluded -> avg is just the recent score.
    assert avg == 0.6


def test_rolling_24h_average_zero_when_only_stubs():
    now = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    art = NewsArticle(
        id="x" * 32,
        source="finnhub",
        ticker="GOLD",
        headline="h",
        body="b",
        url="u",
        published_at=now - timedelta(hours=1),
        body_hash=_hash_body("b"),
    )
    stub = SentimentScore(
        article_id=art.id,
        score=0.5,  # the score is non-zero but confidence=0 -> contributes 0 weight
        label="bullish",
        confidence=0.0,
        model="stub",
        scored_at=now,
    )
    assert nr_mod._rolling_24h_average([(art, stub)], now=now) == 0.0
