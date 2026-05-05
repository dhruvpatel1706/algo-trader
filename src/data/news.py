"""News ingestion (Finnhub company-news endpoint).

Graceful no-op when API key is missing: returns []. Hash-derived stable IDs and
body hashes for dedup + downstream caching (e.g., sentiment scoring).
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

_FINNHUB_URL = "https://finnhub.io/api/v1/company-news"
# Finnhub free tier is 60 calls/min; one second between tickers keeps us safe.
_RATE_LIMIT_SLEEP_SEC = 1.0
_HTTP_TIMEOUT_SEC = 15.0


@dataclass(frozen=True, slots=True)
class NewsArticle:
    id: str  # hash-derived stable ID
    source: str  # "finnhub", "polygon", "manual"
    ticker: str
    headline: str
    body: str
    url: str
    published_at: datetime
    body_hash: str  # used for dedup + caching


def _hash_body(text: str) -> str:
    """Stable SHA-256 hex digest of the body (used for dedup + caching keys)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_id(source: str, ticker: str, url: str, body_hash: str) -> str:
    """Stable, deterministic ID for an article. Same inputs -> same ID across runs."""
    raw = f"{source}|{ticker.upper()}|{url}|{body_hash}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def fetch_finnhub_news(
    tickers: list[str],
    since: datetime,
    api_key: str | None = None,
) -> list[NewsArticle]:
    """Pull from Finnhub's company-news endpoint.

    Default: api_key from env FINNHUB_API_KEY. If unset, return [] (graceful no-op,
    don't crash).

    Rate limit: 60 calls/min. Sleep between tickers to stay under.
    Endpoint: GET https://finnhub.io/api/v1/company-news?symbol=AAPL&from=2024-01-01&to=2024-01-15&token=KEY
    """
    key = api_key if api_key is not None else os.environ.get("FINNHUB_API_KEY")
    if not key:
        return []

    today = datetime.now(tz=UTC).date()
    since_date = since.date() if since.tzinfo else since.replace(tzinfo=UTC).date()

    out: list[NewsArticle] = []
    with httpx.Client(timeout=_HTTP_TIMEOUT_SEC) as client:
        for i, ticker in enumerate(tickers):
            if i > 0:
                time.sleep(_RATE_LIMIT_SLEEP_SEC)
            params = {
                "symbol": ticker.upper(),
                "from": since_date.isoformat(),
                "to": today.isoformat(),
                "token": key,
            }
            try:
                resp = client.get(_FINNHUB_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()
            except (httpx.HTTPError, ValueError):
                # Skip ticker on transient failure; do not crash the whole batch.
                continue

            if not isinstance(payload, list):
                continue

            for item in payload:
                if not isinstance(item, dict):
                    continue
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

                body_hash = _hash_body(body or headline)
                article_id = _make_id("finnhub", ticker, url, body_hash)
                out.append(
                    NewsArticle(
                        id=article_id,
                        source="finnhub",
                        ticker=ticker.upper(),
                        headline=headline,
                        body=body,
                        url=url,
                        published_at=published_at,
                        body_hash=body_hash,
                    )
                )
    return out
