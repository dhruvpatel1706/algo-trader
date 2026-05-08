"""Alt-data confidence multiplier.

Composes three independent equity-only signals into a single
``[0.7, 1.3]`` multiplier that the trade pipeline applies to a signal's
confidence (alongside the analyst and reasoner multipliers).

Sources
-------
1. **SEC Form 4 insider buys** (open-market purchases, code "P"). Cluster
   buying — multiple insiders accumulating in a short window — is the
   strongest classical "smart money" tell that survives every academic
   study going back to Lakonishok & Lee (2001). Sales are ignored
   because of 10b5-1 / scheduled-divestiture noise.
2. **Quiver Congress trades** (when ``QUIVER_API_KEY`` is set). The
   45-day disclosure window means the signal is stale, so we use it as
   a watchlist boost — never the entry trigger. Accumulation by 3+
   members within 30 days is the typical confluence pattern.
3. **Finnhub news sentiment** over the last 24h, scored via
   :func:`src.data.sentiment.score_article`. Anonymised (ticker
   replaced with ``[ASSET_<id>]``) so the LLM doesn't pre-bias on the
   ticker name.

All three sources are optional: if a fetcher is missing, errors, or
returns an empty list, that contribution defaults to neutral (1.0).
The composite multiplier never moves more than ±30% so a single noisy
source can't dominate the sizing math. This is a *confidence
multiplier*, not an entry signal — strategies still emit the trades on
their own rules.

Asset-class scope
-----------------
Form 4 / Congress / company news are all equity-keyed (CIK + ticker).
For non-equity asset classes (crypto, bonds, gold, silver) this module
returns the neutral multiplier (1.0) without any I/O — the bond/gold
agents simply skip this layer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables — calibrated against the v1 plan's "+0.05/+0.10/+0.15" buckets
# ---------------------------------------------------------------------------

# How many days back to scan for Form 4 cluster buys. 14 days is the
# standard window in the academic literature; longer windows dilute the
# clustering signal, shorter windows undercount real accumulation.
_INSIDER_LOOKBACK_DAYS: int = 14

# How many UNIQUE insiders need to have bought in the lookback window
# for the cluster signal to fire. Two is too low (often co-founders);
# three is the textbook threshold.
_INSIDER_CLUSTER_MIN: int = 3

# How many days back to scan for the Congress accumulation signal.
_CONGRESS_LOOKBACK_DAYS: int = 60

# News sentiment lookback. 24h is short enough that the score is still
# reflecting "current narrative" and not stale noise.
_NEWS_LOOKBACK_HOURS: int = 24

# Multiplier deltas. Tight bounds so any one source can't dominate.
_INSIDER_BOOST: float = 0.10  # +10% size when ≥3 insider buys cluster
_CONGRESS_BOOST: float = 0.05  # +5% when Congress watchlist boost > 0.5
_NEWS_BULL_BOOST: float = 0.15  # +15% when last-24h sentiment > +0.5
_NEWS_BEAR_DAMPEN: float = 0.15  # -15% when last-24h sentiment < -0.5

# Hard ceilings on the composite multiplier. ±30% relative to baseline
# is enough to express conviction without rendering the rule confidence
# meaningless.
_MULT_FLOOR: float = 0.7
_MULT_CEILING: float = 1.3

# How long to cache fetcher results in-process. The bot evaluates the
# same symbol up to once per 5 minutes during NYSE RTH; shorter than
# that and we burn API quota for nothing, longer and we miss the
# day-of insider filing that would have reshaped the verdict.
_CACHE_TTL_SECONDS: float = 300.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class AltDataVerdict:
    """Structured record of the multiplier decision. Journal-safe."""

    multiplier: float
    insider_score: float = 0.0  # in [-1, +1]; positive = cluster buys
    congress_score: float = 0.0  # in [0, 1]; from watchlist_boost
    news_score: float = 0.0  # last-24h average, in [-1, +1]
    n_insider_buys: int = 0
    n_congress_buys: int = 0
    n_news_articles: int = 0
    reasoning: str = ""
    contributions: dict[str, float] = field(default_factory=dict)


# Type aliases for the injected fetchers — kept loose (Any) so tests
# can stub without importing the heavy concrete types.
InsiderFetcher = Callable[[str, date], list[Any]]  # ticker, asof -> list[InsiderTransaction]
CongressFetcher = Callable[[str, date], float]  # ticker, asof -> watchlist_boost in [0,1]
NewsSentimentFetcher = Callable[[str, datetime], list[float]]  # ticker, since -> list[scores]


# ---------------------------------------------------------------------------
# In-process TTL cache. Keyed by (source, symbol). Stored value is
# (timestamp_monotonic, fetcher_result).
# ---------------------------------------------------------------------------

_alt_cache: dict[tuple[str, str], tuple[float, Any]] = {}
_alt_cache_lock = Lock()


def _cache_get(source: str, symbol: str) -> Any | None:
    with _alt_cache_lock:
        entry = _alt_cache.get((source, symbol))
        if entry is None:
            return None
        ts, value = entry
        if (time.monotonic() - ts) >= _CACHE_TTL_SECONDS:
            _alt_cache.pop((source, symbol), None)
            return None
        return value


def _cache_put(source: str, symbol: str, value: Any) -> None:
    with _alt_cache_lock:
        _alt_cache[(source, symbol)] = (time.monotonic(), value)


def reset_alt_cache() -> None:
    """Operator-grade tool: clear ALL alt-data caches. Tests use this."""
    with _alt_cache_lock:
        _alt_cache.clear()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_alt_data_multiplier(  # noqa: PLR0912, PLR0915 - composition step orchestrating 3 independent sources
    symbol: str,
    side: str,
    *,
    asset_class: str = "equity",
    asof: date | None = None,
    insider_fetcher: InsiderFetcher | None = None,
    congress_fetcher: CongressFetcher | None = None,
    news_sentiment_fetcher: NewsSentimentFetcher | None = None,
) -> AltDataVerdict:
    """Compose insider + Congress + news into a confidence multiplier.

    Returns a neutral verdict (multiplier=1.0) when:
      - The asset class is not equity (crypto, bonds, gold, silver).
      - All three fetchers are absent/unavailable.
      - The symbol is unknown to every source.

    Returns a clamped multiplier in [0.7, 1.3] otherwise. The components
    that contributed are recorded in :attr:`contributions` for journal
    auditability.

    Parameters
    ----------
    symbol
        Equity ticker, e.g. ``AAPL``. Crypto / bond / commodity symbols
        are passed through with multiplier=1.0.
    side
        ``"buy"`` or ``"sell"``. Currently only ``"buy"`` consumes the
        multiplier — for short signals we'd flip the sign convention,
        but the live bot is long-only so this just neutralises the
        multiplier on shorts (defence against future ambiguity).
    asset_class
        From the agent's class label. Anything other than ``"equity"``
        skips this layer.
    asof
        Optional reference date for look-ahead protection in tests /
        backtests. Defaults to today.
    *_fetcher
        Optional injected fetchers. None = source disabled (no boost
        attempted). Production code wires the real ``src.data.*``
        helpers behind small adapters.
    """
    if asset_class.lower() != "equity":
        return AltDataVerdict(
            multiplier=1.0,
            reasoning=f"alt-data layer skipped: asset_class={asset_class!r}",
        )
    if side.lower() != "buy":
        return AltDataVerdict(
            multiplier=1.0,
            reasoning=f"alt-data layer skipped: side={side!r} not buy",
        )

    asof = asof or datetime.now(UTC).date()
    contributions: dict[str, float] = {}

    insider_score = 0.0
    n_insider_buys = 0
    if insider_fetcher is not None:
        try:
            cached = _cache_get("insider", symbol)
            if cached is None:
                cached = insider_fetcher(symbol, asof)
                _cache_put("insider", symbol, cached)
            insider_score, n_insider_buys = _score_insider_cluster(cached, asof)
            if insider_score > 0:
                contributions["insider"] = _INSIDER_BOOST * insider_score
        except Exception as e:
            log.warning("alt_data: insider fetch for %s failed: %s", symbol, e)

    congress_score = 0.0
    n_congress = 0  # We don't currently expose the count from watchlist_boost
    if congress_fetcher is not None:
        try:
            cached = _cache_get("congress", symbol)
            if cached is None:
                cached = float(congress_fetcher(symbol, asof))
                _cache_put("congress", symbol, cached)
            congress_score = float(cached)
            if congress_score > 0.5:
                contributions["congress"] = _CONGRESS_BOOST * (
                    (congress_score - 0.5) / 0.5
                )
        except Exception as e:
            log.warning("alt_data: congress fetch for %s failed: %s", symbol, e)

    news_score = 0.0
    n_news = 0
    if news_sentiment_fetcher is not None:
        try:
            cached = _cache_get("news", symbol)
            if cached is None:
                since = datetime.now(UTC) - timedelta(hours=_NEWS_LOOKBACK_HOURS)
                cached = list(news_sentiment_fetcher(symbol, since))
                _cache_put("news", symbol, cached)
            scores = list(cached)
            n_news = len(scores)
            if scores:
                news_score = sum(scores) / len(scores)
                if news_score > 0.5:
                    contributions["news_bull"] = _NEWS_BULL_BOOST * (
                        (news_score - 0.5) / 0.5
                    )
                elif news_score < -0.5:
                    contributions["news_bear"] = -_NEWS_BEAR_DAMPEN * (
                        (-0.5 - news_score) / 0.5
                    )
        except Exception as e:
            log.warning("alt_data: news fetch for %s failed: %s", symbol, e)

    # Sum contributions on top of 1.0 baseline, then clamp.
    raw_multiplier = 1.0 + sum(contributions.values())
    multiplier = max(_MULT_FLOOR, min(_MULT_CEILING, raw_multiplier))

    # Reasoning string for the journal — short and dashboard-friendly.
    parts = []
    if "insider" in contributions:
        parts.append(f"insider+{contributions['insider']:+.2f} ({n_insider_buys} buys)")
    if "congress" in contributions:
        parts.append(f"congress+{contributions['congress']:+.2f}")
    if "news_bull" in contributions:
        parts.append(f"news+{contributions['news_bull']:+.2f} ({news_score:+.2f})")
    if "news_bear" in contributions:
        parts.append(f"news{contributions['news_bear']:+.2f} ({news_score:+.2f})")
    if not parts:
        reasoning = "alt-data: no qualifying source fired; multiplier=1.0"
    else:
        reasoning = (
            f"alt-data on {symbol}: "
            + " | ".join(parts)
            + f" -> mult={multiplier:.2f}"
        )

    return AltDataVerdict(
        multiplier=multiplier,
        insider_score=insider_score,
        congress_score=congress_score,
        news_score=news_score,
        n_insider_buys=n_insider_buys,
        n_congress_buys=n_congress,
        n_news_articles=n_news,
        reasoning=reasoning,
        contributions=contributions,
    )


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _score_insider_cluster(
    transactions: list[Any], asof: date
) -> tuple[float, int]:
    """Return (score, n_open_market_buys) over the lookback window.

    Score in [0, 1]:
      - 0.0  if fewer than ``_INSIDER_CLUSTER_MIN`` unique buyers
      - 1.0  at exactly the threshold
      - >1.0 if more (capped at 2.0 in scoring; the multiplier will
        clamp anyway). Linear above the threshold so 4 buys is bigger
        than 3 but 6 is not 2x as big as 3.

    Notes:
      - Only ``transaction_code == "P"`` (open-market buys) count.
      - Sales (``"S"``) and grants (``"A"``) are ignored — too noisy
        for our purposes.
      - Filings *after* ``asof`` are excluded (look-ahead protection
        in backtests / replays).
    """
    if not transactions:
        return 0.0, 0

    cutoff = asof - timedelta(days=_INSIDER_LOOKBACK_DAYS)
    unique_buyers: set[str] = set()
    n_buys = 0
    for txn in transactions:
        # Tolerate either dict-shape or dataclass-shape entries.
        code = (
            getattr(txn, "transaction_code", None)
            or (txn.get("transaction_code") if isinstance(txn, dict) else None)
        )
        if code != "P":
            continue
        filing_date = (
            getattr(txn, "filing_date", None)
            or (txn.get("filing_date") if isinstance(txn, dict) else None)
        )
        if filing_date is None or filing_date < cutoff or filing_date > asof:
            continue
        filer = (
            getattr(txn, "filer", None)
            or (txn.get("filer") if isinstance(txn, dict) else None)
            or "<unknown>"
        )
        unique_buyers.add(filer)
        n_buys += 1

    if len(unique_buyers) < _INSIDER_CLUSTER_MIN:
        return 0.0, n_buys
    # Score is 1.0 at threshold, linearly higher up to 2.0 at 2x threshold.
    over = len(unique_buyers) - _INSIDER_CLUSTER_MIN
    score = min(2.0, 1.0 + over / _INSIDER_CLUSTER_MIN)
    return score, n_buys
