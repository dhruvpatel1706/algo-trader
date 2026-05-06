"""``GET /api/feeds/status`` — what's wired up.

Snapshot of every external integration the bot can use, plus whether its
credentials are present in the environment. Used by the dashboard's
"connected feeds" chip strip so the operator can see at a glance which
data sources / LLM providers / brokers are reachable.

Privacy
-------
This endpoint NEVER returns key material. For each configured key we expose:

- ``configured: bool``        — non-empty env var present
- ``preview: str | None``     — last 4 chars only (e.g. ``"…NTY7"``), or None
                                 when not configured. Useful for the operator
                                 to spot when a key got rotated without
                                 leaking the key.
- ``required_for: list[str]`` — features that depend on this key
- ``category: str``           — group label for the UI ("broker", "llm",
                                 "news", "altdata", "alerts")

We deliberately do NOT call out to the providers to test the keys — that
would put the dashboard's healthcheck on the critical path of every
external API's availability and rate limit. Reachability is observed
indirectly via the bot's regular use (e.g. ``autonomous_reasoner_eval``
journal entries with ``provider`` field show which LLM is actually
serving traffic).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


@dataclass(frozen=True)
class _FeedSpec:
    """One row in the integrations catalog."""

    name: str
    env_var: str
    category: str
    required_for: tuple[str, ...]


# Catalog ordered by operational importance — broker first (without it the bot
# can't trade), then LLM (without it reasoning falls back to identity multiplier),
# then alt-data (filters), then alerts.
_FEEDS: tuple[_FeedSpec, ...] = (
    _FeedSpec(
        name="Alpaca (paper)",
        env_var="ALPACA_API_KEY",
        category="broker",
        required_for=("equity orders", "gold/bonds orders", "crypto orders", "portfolio data"),
    ),
    _FeedSpec(
        name="Alpaca secret",
        env_var="ALPACA_SECRET_KEY",
        category="broker",
        required_for=("Alpaca authentication",),
    ),
    _FeedSpec(
        name="Anthropic",
        env_var="ANTHROPIC_API_KEY",
        category="llm",
        required_for=("autonomous reasoner (chain pos 2)", "watchdog diagnosis"),
    ),
    _FeedSpec(
        name="Gemini",
        env_var="GEMINI_API_KEY",
        category="llm",
        required_for=("autonomous reasoner (chain pos 1, free tier)",),
    ),
    _FeedSpec(
        name="OpenAI",
        env_var="OPENAI_API_KEY",
        category="llm",
        required_for=("autonomous reasoner (chain pos 3)", "OpenAI embeddings (memory)"),
    ),
    _FeedSpec(
        name="Finnhub",
        env_var="FINNHUB_API_KEY",
        category="news",
        required_for=("news ingestion (free tier)",),
    ),
    _FeedSpec(
        name="Polygon news",
        env_var="POLYGON_NEWS_KEY",
        category="news",
        required_for=("news ingestion (paid tier upgrade)",),
    ),
    _FeedSpec(
        name="Polygon stocks",
        env_var="POLYGON_STOCKS_KEY",
        category="data",
        required_for=("intraday equity bars (deferred to v2)",),
    ),
    _FeedSpec(
        name="Polygon options",
        env_var="POLYGON_OPTIONS_KEY",
        category="data",
        required_for=("options chains for wheel_etf (deferred to v2)",),
    ),
    _FeedSpec(
        name="Quiver (Congress)",
        env_var="QUIVER_API_KEY",
        category="altdata",
        required_for=("Congressional trades feed",),
    ),
    _FeedSpec(
        name="Nansen (smart money)",
        env_var="NANSEN_API_KEY",
        category="altdata",
        required_for=("crypto wallet shadow-copy",),
    ),
    _FeedSpec(
        name="Coinbase",
        env_var="COINBASE_API_KEY",
        category="broker",
        required_for=("Coinbase as crypto broker (US-friendly upgrade)",),
    ),
    _FeedSpec(
        name="Discord webhook",
        env_var="DISCORD_WEBHOOK_URL",
        category="alerts",
        required_for=("WARN+ alert delivery",),
    ),
)


class FeedStatus(BaseModel):
    name: str
    env_var: str
    category: str
    required_for: list[str]
    configured: bool
    preview: str | None  # last 4 chars when configured, None otherwise


class FeedsStatusResponse(BaseModel):
    feeds: list[FeedStatus]
    n_configured: int
    n_total: int


def _preview(secret: str) -> str | None:
    """Last 4 chars of the key, or None if too short to be a real key."""
    s = secret.strip()
    if len(s) < 8:
        # Refuse to preview anything shorter than 8 chars — too risky to leak.
        return None
    return f"…{s[-4:]}"


def _read_status() -> FeedsStatusResponse:
    """Pure function — exposed for tests."""
    out: list[FeedStatus] = []
    n_configured = 0
    for spec in _FEEDS:
        raw = os.environ.get(spec.env_var, "")
        configured = bool(raw and raw.strip())
        if configured:
            n_configured += 1
        out.append(
            FeedStatus(
                name=spec.name,
                env_var=spec.env_var,
                category=spec.category,
                required_for=list(spec.required_for),
                configured=configured,
                preview=_preview(raw) if configured else None,
            )
        )
    return FeedsStatusResponse(feeds=out, n_configured=n_configured, n_total=len(_FEEDS))


@router.get("/api/feeds/status", response_model=FeedsStatusResponse)
def feeds_status() -> FeedsStatusResponse:
    """Snapshot of which integrations have credentials in the environment.

    Reads ``os.environ`` once per call — keys can be rotated without a
    backend restart and the next call picks them up. Never returns key
    material; only ``configured: bool`` and a 4-char tail preview.
    """
    return _read_status()
