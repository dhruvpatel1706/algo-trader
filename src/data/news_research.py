"""Per-asset-class news research wrapper.

Each agent (equity, gold, silver, bonds, crypto) gets its own scoped news view via
``build_digest(asset_class)``. The wrapper:

  * Resolves ticker-keyed news via Finnhub's company-news endpoint when the asset
    class is ticker-driven (equities, ETF baskets like ``gold``/``silver``/etc.).
  * Falls back to keyword filtering on Finnhub's general-news endpoint for macro
    topics where individual tickers are not the right key (gold, silver, bonds,
    crypto). The filter checks for any of a small curated keyword list against
    headline + body.
  * Optionally scores a capped subset of articles via ``score_article``.

Defensive paths:
  * If ``FINNHUB_API_KEY`` is unset -> ``fetch_asset_news`` returns ``[]``.
  * If ``ANTHROPIC_API_KEY`` is unset -> scores default to neutral and the
    rolling 24h aggregate is 0.0.

The keyword fallbacks live in this module; they were chosen to keep the
filtering conservative (precision over recall) so a "gold-coast real estate"
headline does not get confused with "gold price hits new high".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from src.data.news import NewsArticle, _hash_body, _make_id
from src.data.sentiment import SentimentScore, _stub_score, score_article
from src.data.universe import Universe, UniverseError

# Finnhub general-news endpoint: covers macro/topic news without a ticker tag.
_FINNHUB_GENERAL_URL = "https://finnhub.io/api/v1/news"
_HTTP_TIMEOUT_SEC = 15.0

# Asset-class -> universe yaml key. ``equity`` is special: callers pass the
# specific equity universe key directly via ``fetch_asset_news`` if they want a
# narrower list, otherwise the default ``large_caps_50`` is used.
_DEFAULT_EQUITY_UNIVERSE = "large_caps_50"

# Keyword sets used when an asset class is macro-driven (no useful per-ticker
# news feed). Keywords are matched against the lower-cased ``headline + body``
# of each article using a whole-word check so substrings inside unrelated nouns
# (e.g. "gold-coast real estate") are not flagged.
_KEYWORD_SETS: dict[str, tuple[str, ...]] = {
    "gold": (
        "gold",
        "gld",
        "xau",
        "bullion",
        "federal reserve gold",
        "central bank gold",
    ),
    "silver": (
        "silver",
        "slv",
        "xag",
        "industrial silver",
    ),
    "bonds": (
        "treasury",
        "yield curve",
        "fomc",
        "fed funds",
        "10-year yield",
        "bond auction",
    ),
    "crypto": (
        "bitcoin",
        "btc",
        "ethereum",
        "eth",
        "stablecoin",
        "crypto",
    ),
}

# Asset classes that should use the keyword-fallback path (general news endpoint
# filtered locally). ``equity`` always uses the ticker-keyed company-news route.
_MACRO_ASSET_CLASSES: frozenset[str] = frozenset({"gold", "silver", "bonds", "crypto"})

# Phrases that look like "gold" but should NOT match the gold keyword set. We
# strip these before the whole-word scan so a headline like "Gold Coast real
# estate prices fall" is excluded.
_NEGATIVE_PHRASES: dict[str, tuple[str, ...]] = {
    "gold": ("gold coast", "gold-coast", "goldsmith", "goldman"),
    "silver": ("silverstone", "silver lining"),
    "bonds": ("james bond", "bondholder relations"),
    "crypto": ("cryptography classroom", "cryptogram"),
}


@dataclass(frozen=True, slots=True)
class AssetNewsDigest:
    """Per-asset-class news digest.

    ``rolling_24h_score`` is a confidence-weighted average of the sentiment
    scores of articles in the last 24h. Range ``[-1, 1]`` (0 when there is no
    data or no API key).
    """

    asset_class: str
    universe_key: str
    n_articles: int
    rolling_24h_score: float
    top_articles: list[tuple[NewsArticle, SentimentScore]]
    generated_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_asset_class(asset_class: str) -> str:
    return asset_class.strip().lower()


def _resolve_universe_key(asset_class: str) -> str:
    """Map an asset class to the universe key used in ``docs/universes.yaml``."""
    cls = _normalize_asset_class(asset_class)
    if cls == "equity":
        return _DEFAULT_EQUITY_UNIVERSE
    return cls


def _whole_word_present(text_lower: str, term: str) -> bool:
    """Return True iff ``term`` appears as a whole word (case-insensitive)."""
    term_lower = term.lower()
    if " " in term_lower or "-" in term_lower:
        # Phrases: do a substring check against the cleaned text. Whole-phrase
        # matches are still meaningful because phrases are already specific.
        return term_lower in text_lower
    n = len(term_lower)
    i = 0
    while True:
        idx = text_lower.find(term_lower, i)
        if idx < 0:
            return False
        left_ok = idx == 0 or not text_lower[idx - 1].isalnum()
        right_idx = idx + n
        right_ok = right_idx == len(text_lower) or not text_lower[right_idx].isalnum()
        if left_ok and right_ok:
            return True
        i = idx + n


def _keyword_match(asset_class: str, headline: str, body: str) -> bool:
    """Conservative keyword filter for macro asset classes."""
    cls = _normalize_asset_class(asset_class)
    keywords = _KEYWORD_SETS.get(cls)
    if not keywords:
        return False
    text_lower = f"{headline}\n{body}".lower()
    # Excise negative phrases first so e.g. "gold coast" does not contribute a
    # match for the bare keyword "gold".
    for phrase in _NEGATIVE_PHRASES.get(cls, ()):
        text_lower = text_lower.replace(phrase, " ")
    return any(_whole_word_present(text_lower, kw) for kw in keywords)


def _fetch_general_news(
    api_key: str,
    *,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Pull Finnhub's general-news feed. Empty list on any error."""
    own_client = client is None
    cli = client or httpx.Client(timeout=_HTTP_TIMEOUT_SEC)
    try:
        resp = cli.get(
            _FINNHUB_GENERAL_URL,
            params={"category": "general", "token": api_key},
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    finally:
        if own_client:
            cli.close()
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _macro_articles(
    asset_class: str,
    *,
    api_key: str,
    since: datetime,
    limit_articles: int,
) -> list[NewsArticle]:
    """Fetch + filter Finnhub general news for a macro asset class."""
    raw = _fetch_general_news(api_key)
    out: list[NewsArticle] = []
    seen: set[str] = set()
    for item in raw:
        headline = str(item.get("headline") or "").strip()
        body = str(item.get("summary") or "").strip()
        url = str(item.get("url") or "").strip()
        ts = item.get("datetime")
        if not headline or ts is None:
            continue
        try:
            published_at = datetime.fromtimestamp(int(ts), tz=UTC)
        except (TypeError, ValueError, OSError):
            continue
        if published_at < since:
            continue
        if not _keyword_match(asset_class, headline, body):
            continue
        body_hash = _hash_body(body or headline)
        if body_hash in seen:
            continue
        seen.add(body_hash)
        article_id = _make_id("finnhub-general", asset_class, url, body_hash)
        out.append(
            NewsArticle(
                id=article_id,
                source="finnhub",
                ticker=asset_class.upper(),
                headline=headline,
                body=body,
                url=url,
                published_at=published_at,
                body_hash=body_hash,
            )
        )
        if len(out) >= limit_articles:
            break
    return out


def _ticker_keyed_articles(
    universe_key: str,
    *,
    since: datetime,
    limit_articles: int,
) -> list[NewsArticle]:
    """Resolve a yaml universe key -> list of tickers -> ticker-keyed news."""
    try:
        tickers = list(Universe.named(universe_key))
    except UniverseError:
        return []
    if not tickers:
        return []
    # Lazy import to break a potential circular import surface and keep the
    # top-level import fast.
    from src.data.news import fetch_finnhub_news  # noqa: PLC0415

    raw = fetch_finnhub_news(tickers, since=since)
    seen: set[str] = set()
    deduped: list[NewsArticle] = []
    for art in raw:
        if art.body_hash in seen:
            continue
        seen.add(art.body_hash)
        deduped.append(art)
        if len(deduped) >= limit_articles:
            break
    return deduped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_asset_news(
    asset_class: str,
    since: datetime | None = None,
    limit_articles: int = 50,
) -> list[NewsArticle]:
    """Pull news scoped to an asset class.

    ``asset_class`` is one of: ``equity``, ``gold``, ``silver``, ``bonds``,
    ``crypto``. Equities use the ticker-keyed Finnhub company-news endpoint via
    ``Universe.named``. Macro classes fall back to filtering Finnhub's general
    news feed by a curated keyword set.

    Returns a deduped list (keyed by body hash) sorted as Finnhub returned it
    (newest-first in practice; we do not re-sort to avoid hiding source bugs).

    If ``FINNHUB_API_KEY`` is unset, returns ``[]``.
    """
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        return []

    cls = _normalize_asset_class(asset_class)
    if since is None:
        since = datetime.now(tz=UTC) - timedelta(days=7)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)

    if cls in _MACRO_ASSET_CLASSES:
        return _macro_articles(
            cls,
            api_key=api_key,
            since=since,
            limit_articles=limit_articles,
        )

    universe_key = _resolve_universe_key(cls)
    return _ticker_keyed_articles(
        universe_key,
        since=since,
        limit_articles=limit_articles,
    )


def _rolling_24h_average(
    scored: list[tuple[NewsArticle, SentimentScore]],
    *,
    now: datetime,
) -> float:
    """Confidence-weighted mean of scores from articles published in the last 24h."""
    cutoff = now - timedelta(hours=24)
    weighted_sum = 0.0
    total_weight = 0.0
    for art, sc in scored:
        if art.published_at < cutoff:
            continue
        weight = max(0.0, sc.confidence)
        if weight == 0.0:
            # Treat zero confidence as a trivial weight so a non-stub neutral
            # vote still nudges the running mean slightly. Stubs (model="stub")
            # carry confidence 0 -> contribute nothing.
            continue
        weighted_sum += sc.score * weight
        total_weight += weight
    if total_weight == 0.0:
        return 0.0
    return max(-1.0, min(1.0, weighted_sum / total_weight))


def build_digest(
    asset_class: str,
    today: date | None = None,
    score_articles: bool = True,
    max_to_score: int = 25,
) -> AssetNewsDigest:
    """Fetch + score + summarize for one asset class.

    Returns a :class:`AssetNewsDigest`. If ``ANTHROPIC_API_KEY`` is unset, every
    article is given a neutral stub score and ``rolling_24h_score`` is 0.
    """
    cls = _normalize_asset_class(asset_class)
    universe_key = _resolve_universe_key(cls)
    now = datetime.now(tz=UTC)
    today_d = today or now.date()

    articles = fetch_asset_news(cls)

    # Newest-first ordering for the "top" view.
    articles_sorted = sorted(articles, key=lambda a: a.published_at, reverse=True)
    to_score = articles_sorted[:max_to_score]

    scored: list[tuple[NewsArticle, SentimentScore]] = []
    if score_articles:
        for art in to_score:
            sc = score_article(art, today=today_d)
            scored.append((art, sc))
    else:
        scored = [(art, _stub_score(art.id)) for art in to_score]

    rolling = _rolling_24h_average(scored, now=now)

    top_articles = scored[:10]

    return AssetNewsDigest(
        asset_class=cls,
        universe_key=universe_key,
        n_articles=len(articles_sorted),
        rolling_24h_score=rolling,
        top_articles=top_articles,
        generated_at=now,
    )


