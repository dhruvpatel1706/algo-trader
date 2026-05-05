"""Congressional trading data ingestion + watchlist boost scoring.

Pulls disclosed Congressional stock trades (House STOCK Act / Senate ETHICS
filings) and converts them into a 0..1 *watchlist boost* that strategies
multiply into signal confidence — never an entry trigger on its own.

Look-ahead protection: scoring uses ``filing_date`` (the disclosure date),
NEVER ``txn_date`` (the underlying trade), because the trade isn't observable
to outside traders until the filing surfaces — typically 30-45 days later.

Default path: Quiver Quantitative if ``QUIVER_API_KEY`` is set in env or
passed explicitly. If neither is provided, the fetch returns an empty list
gracefully (no crash) — Congress alt-data is optional per project defaults.

Direct scraping of the House/Senate disclosure portals is OUT OF SCOPE for
v1; the function shape allows adding scrapers later without changing callers.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

logger = logging.getLogger(__name__)

QUIVER_BASE_URL = "https://api.quiverquant.com/beta/historical/congresstrading"
_REQUEST_TIMEOUT = 30  # seconds


@dataclass(frozen=True, slots=True)
class CongressTrade:
    """A single disclosed Congressional trade.

    ``filing_date`` is the date the disclosure became public — that's the
    only timestamp safe to use for backtests. ``txn_date`` is the underlying
    trade date and is informational only (used to derive the late-filer flag).
    """

    ticker: str
    member: str
    party: Literal["D", "R", "I"]
    chamber: Literal["house", "senate"]
    committee: str | None
    txn_date: date
    filing_date: date  # ALWAYS use filing_date for backtests
    amount_low: float  # disclosed dollar range minimum
    amount_high: float
    type: Literal["P", "S"]  # Purchase or Sale (mapped from disclosure type)


# ----------------------------------------------------------------------------
# Network seam (kept tiny so tests can monkeypatch in one place).
# ----------------------------------------------------------------------------


def _fetch_url(url: str, api_key: str) -> bytes:
    """Fetch a URL with the Quiver bearer-token header.

    Tests monkeypatch this function to avoid network calls.
    """
    req = urllib.request.Request(  # noqa: S310 — Quiver https only
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # noqa: S310
        return resp.read()


# ----------------------------------------------------------------------------
# Parsing.
# ----------------------------------------------------------------------------


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    # Quiver returns "YYYY-MM-DD" most of the time; accept ISO timestamps too.
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Last-ditch: take the leading 10 chars and try ISO date.
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize_party(raw: str) -> Literal["D", "R", "I"]:
    if not raw:
        return "I"
    head = raw.strip()[:1].upper()
    if head in {"D", "R"}:
        return head  # type: ignore[return-value]
    return "I"


def _normalize_chamber(raw: str) -> Literal["house", "senate"]:
    s = (raw or "").strip().lower()
    if "senate" in s or s == "s":
        return "senate"
    return "house"


def _normalize_type(raw: str) -> Literal["P", "S"] | None:
    """Map Quiver disclosure types to ``P`` (buy) or ``S`` (sell).

    Quiver uses strings like ``"Purchase"``, ``"Sale (Full)"``, ``"Sale (Partial)"``,
    ``"Exchange"``. Returns ``None`` for ambiguous types so callers can skip
    rather than guess.
    """
    if not raw:
        return None
    s = raw.strip().lower()
    if s.startswith("p") or "purchase" in s or "buy" in s:
        return "P"
    if s.startswith("s") or "sale" in s or "sell" in s:
        return "S"
    return None


def _parse_amount(raw: str | float | int | None) -> tuple[float, float]:
    """Disclosure ranges are strings like ``"$1,001 - $15,000"``.

    Returns ``(low, high)``; if a single number is given, both are equal.
    Returns ``(0.0, 0.0)`` on parse failure — the caller can still record the
    trade but the watchlist value subscore won't reward it.
    """
    if raw is None:
        return 0.0, 0.0
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v, v
    s = str(raw).replace("$", "").replace(",", "").strip()
    if not s:
        return 0.0, 0.0
    parts = [p.strip() for p in s.split("-")]
    try:
        if len(parts) == 1:
            v = float(parts[0])
            return v, v
        low = float(parts[0])
        high = float(parts[-1])
        return low, high
    except ValueError:
        return 0.0, 0.0


def _parse_trade(record: dict) -> CongressTrade | None:
    """Convert one Quiver record into a CongressTrade. Returns ``None`` on bad data."""
    ticker = str(record.get("Ticker", "") or record.get("ticker", "")).upper().strip()
    member = str(record.get("Representative", "") or record.get("member", "")).strip()
    if not ticker or not member:
        return None

    txn_d = _parse_date(
        str(record.get("TransactionDate", "") or record.get("txn_date", ""))
    )
    filing_d = _parse_date(
        str(record.get("ReportDate", "") or record.get("filing_date", ""))
    )
    if txn_d is None or filing_d is None:
        return None

    type_ = _normalize_type(
        str(record.get("Transaction", "") or record.get("type", ""))
    )
    if type_ is None:
        return None

    low, high = _parse_amount(
        record.get("Range") or record.get("Amount") or record.get("amount")
    )
    party = _normalize_party(str(record.get("Party", "") or record.get("party", "")))
    chamber = _normalize_chamber(
        str(record.get("Chamber", "") or record.get("House", "") or "")
    )
    committee_raw = record.get("Committee") or record.get("committee")
    committee = str(committee_raw).strip() if committee_raw else None

    return CongressTrade(
        ticker=ticker,
        member=member,
        party=party,
        chamber=chamber,
        committee=committee,
        txn_date=txn_d,
        filing_date=filing_d,
        amount_low=low,
        amount_high=high,
        type=type_,
    )


# ----------------------------------------------------------------------------
# Fetch.
# ----------------------------------------------------------------------------


def fetch_congress_trades(
    tickers: list[str] | None = None,
    days: int = 90,
    api_key: str | None = None,
) -> list[CongressTrade]:
    """Pull recent Congressional trades.

    Default path: Quiver Quantitative if ``api_key`` provided OR
    ``QUIVER_API_KEY`` env var is set.

    Endpoint: ``GET https://api.quiverquant.com/beta/historical/congresstrading/{ticker}``

    Parameters
    ----------
    tickers:
        If provided, queries Quiver per-ticker. ``None`` returns an empty
        list (Quiver requires a ticker; bulk endpoint is gated separately).
    days:
        Maximum age (by ``filing_date``) of trades to keep.
    api_key:
        Explicit Quiver token. Falls back to ``QUIVER_API_KEY`` env var.

    Returns
    -------
    list[CongressTrade]
        Empty list if no API key is available (graceful degradation — never
        crashes), if the network call fails, or if no trades match.
    """
    key = api_key or os.environ.get("QUIVER_API_KEY") or ""
    if not key:
        # Gated alt-data: silently degrade per project default.
        return []

    if not tickers:
        # Quiver's historical endpoint is keyed by ticker; without a ticker
        # list there's nothing to fetch on the cheap path.
        return []

    cutoff = date.today() - timedelta(days=days)
    out: list[CongressTrade] = []

    for raw_ticker in tickers:
        ticker = raw_ticker.upper().strip()
        if not ticker:
            continue
        url = f"{QUIVER_BASE_URL}/{urllib.parse.quote(ticker)}"
        try:
            body = _fetch_url(url, api_key=key)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Quiver fetch failed for %s: %s", ticker, exc)
            continue

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
            logger.warning("Quiver parse failed for %s: %s", ticker, exc)
            continue

        if not isinstance(payload, list):
            continue

        for record in payload:
            if not isinstance(record, dict):
                continue
            trade = _parse_trade(record)
            if trade is None:
                continue
            if trade.filing_date < cutoff:
                continue
            out.append(trade)

    return out


# ----------------------------------------------------------------------------
# Scoring.
# ----------------------------------------------------------------------------


def _clip01(x: float) -> float:
    return 0.0 if x <= 0 else (1.0 if x >= 1 else x)


def watchlist_boost(
    ticker: str,
    trades: list[CongressTrade],
    asof: date,
    accumulation_days: int = 60,
    cluster_min: int = 3,
) -> float:
    """0..1 watchlist boost for ``ticker`` based on Congressional buying.

    Subscores
    ---------
    * **cluster** (0..1): unique buyers (``type=='P'``) inside the last 30 days
      before ``asof`` (by ``filing_date``), normalised against ``cluster_min``.
    * **value** (0..1): aggregate ``amount_low`` (conservative dollar floor) over
      the ``accumulation_days`` window, normalised against $500K (saturates).
    * **late_filer** (0..1): fraction of recent buys whose ``filing_date`` was
      more than 45 days after ``txn_date``. Late filings are noisier and
      occasionally signal urgency the member tried to obscure.

    Sales reduce conviction — ``cluster`` only counts purchases, so a wash of
    sales with no buys returns 0.0.

    Filings dated *after* ``asof`` are excluded (look-ahead protection).

    Returns
    -------
    ``0.5 * cluster + 0.35 * value + 0.15 * late_filer``, clipped to ``[0, 1]``.

    NOT an entry trigger — multiply into signal confidence:
        ``signal.confidence *= (1 + 0.15 * watchlist_boost(...))``
    """
    if not trades:
        return 0.0

    ticker_u = ticker.upper()
    cluster_start = asof - timedelta(days=30)
    accum_start = asof - timedelta(days=accumulation_days)

    cluster_buyers: set[str] = set()
    accum_value = 0.0
    accum_buys = 0
    late_filer_buys = 0

    for t in trades:
        if t.ticker != ticker_u:
            continue
        if t.filing_date > asof:
            continue  # Look-ahead protection.
        if t.type != "P":
            continue  # Boost only on purchases; sales don't add conviction.

        if t.filing_date >= cluster_start:
            cluster_buyers.add(t.member)
        if t.filing_date >= accum_start:
            accum_value += max(t.amount_low, 0.0)
            accum_buys += 1
            if (t.filing_date - t.txn_date).days > 45:
                late_filer_buys += 1

    if not cluster_buyers and accum_buys == 0:
        return 0.0

    cluster_score = _clip01(len(cluster_buyers) / max(cluster_min, 1))
    # $500K saturates the value subscore — disclosure ranges are wide and
    # ``amount_low`` is intentionally conservative.
    value_score = _clip01(accum_value / 500_000.0)
    late_score = _clip01(late_filer_buys / accum_buys) if accum_buys else 0.0

    score = 0.5 * cluster_score + 0.35 * value_score + 0.15 * late_score
    return _clip01(score)


__all__ = [
    "CongressTrade",
    "fetch_congress_trades",
    "watchlist_boost",
]
