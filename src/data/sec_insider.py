"""SEC Form 4 (insider transactions) ingestion and scoring.

Pulls recent Form 4 filings from SEC EDGAR, parses them into
``InsiderTransaction`` records, and computes a per-ticker insider-buying
score that strategies can multiply into signal confidence.

Look-ahead protection: we always key timing off ``filing_date`` (when the
filing became public), never ``txn_date`` (the underlying trade date).

Notes:
    * EDGAR rate limit is 10 req/sec and a descriptive ``User-Agent`` header
      is required. We use ``urllib`` + stdlib XML parsing to keep the
      dependency surface minimal.
    * Every parse error is caught and logged - one malformed filing must
      never crash the caller. We skip and continue.
    * Tests must NOT hit the network; use the on-disk fixture loaders or
      monkeypatch ``_fetch_url``.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

logger = logging.getLogger(__name__)

# SEC Form 4 transaction codes. We only score "P" (open-market buy).
TransactionCode = Literal[
    "P", "S", "A", "M", "F", "G", "I", "C", "D", "K", "L", "X", "U"
]
_VALID_CODES: frozenset[str] = frozenset(
    ["P", "S", "A", "M", "F", "G", "I", "C", "D", "K", "L", "X", "U"]
)

DEFAULT_USER_AGENT = "algo-trader research dhruv17062000@gmail.com"
EDGAR_RECENT_FEED = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&output=atom"
)

# Atom namespace used by EDGAR's recent-filings feed.
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass(frozen=True, slots=True)
class InsiderTransaction:
    """A single insider transaction line from a Form 4 XML filing."""

    ticker: str
    filer: str
    role: str  # "Director", "Officer", "10% Owner", etc.
    txn_date: date
    filing_date: date  # ALWAYS use this for backtests (look-ahead protection)
    transaction_code: TransactionCode
    shares: int
    price_per_share: float
    value: float
    plan_flag: bool  # True if filing references a 10b5-1 trading plan


# ----------------------------------------------------------------------------
# Network helpers (kept tiny so tests can monkeypatch a single seam).
# ----------------------------------------------------------------------------


def _fetch_url(url: str, user_agent: str) -> bytes:
    """Fetch a URL with the SEC-required User-Agent.

    Tests monkeypatch this function to avoid network calls.
    """
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})  # noqa: S310 — SEC EDGAR https only
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — https URL
        return resp.read()


# ----------------------------------------------------------------------------
# Parsing.
# ----------------------------------------------------------------------------


def _text(elem: ET.Element | None) -> str:
    """Strip + None-safe text getter."""
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _value(parent: ET.Element | None, tag: str) -> str:
    """Many Form 4 fields use ``<field><value>X</value></field>``.

    Some legacy filings put the text directly on ``<field>`` instead.
    We tolerate both shapes.
    """
    if parent is None:
        return ""
    child = parent.find(tag)
    if child is None:
        return ""
    val = child.find("value")
    if val is not None:
        return _text(val)
    return _text(child)


def _parse_role(reporting_owner: ET.Element | None) -> str:
    """Build a human-readable role string from ``reportingOwnerRelationship``."""
    if reporting_owner is None:
        return ""
    rel = reporting_owner.find("reportingOwnerRelationship")
    if rel is None:
        return ""
    parts: list[str] = []
    if _text(rel.find("isDirector")) in {"1", "true"}:
        parts.append("Director")
    if _text(rel.find("isOfficer")) in {"1", "true"}:
        title = _text(rel.find("officerTitle"))
        parts.append(f"Officer ({title})" if title else "Officer")
    if _text(rel.find("isTenPercentOwner")) in {"1", "true"}:
        parts.append("10% Owner")
    if _text(rel.find("isOther")) in {"1", "true"}:
        other = _text(rel.find("otherText"))
        parts.append(other or "Other")
    return ", ".join(parts)


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_float(s: str) -> float:
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _to_int(s: str) -> int:
    if not s:
        return 0
    try:
        # Some filings emit fractional share counts (e.g. DRIP). Truncate.
        return int(float(s))
    except ValueError:
        return 0


def parse_form4_xml(xml_bytes: bytes, filing_date: date) -> list[InsiderTransaction]:
    """Parse one Form 4 ``ownershipDocument`` XML blob.

    Returns an empty list (and logs) on any structural failure - we never
    raise, because a single malformed filing shouldn't break a batch.
    """
    try:
        root = ET.fromstring(xml_bytes)  # noqa: S314 - SEC content, XML only
    except ET.ParseError as exc:
        logger.warning("Form 4 XML parse failed: %s", exc)
        return []

    # Issuer (ticker + name).
    issuer = root.find("issuer")
    ticker = _text(issuer.find("issuerTradingSymbol")) if issuer is not None else ""
    ticker = ticker.upper()

    # First reportingOwner is the filer; Form 4 can list more than one but the
    # primary is what dashboards display.
    reporting_owner = root.find("reportingOwner")
    filer = ""
    if reporting_owner is not None:
        filer = _value(reporting_owner.find("reportingOwnerId"), "rptOwnerName")
    role = _parse_role(reporting_owner)

    # 10b5-1 plan flag. Form 4 v5 has an explicit ``<rule10b5-1Flag>`` field.
    # Older filings encode it in footnotes - we look in both places.
    plan_flag = False
    rule_flag = root.find(".//rule10b5-1Flag/value")
    if rule_flag is not None and _text(rule_flag) in {"1", "true"}:
        plan_flag = True
    if not plan_flag:
        for fn in root.findall(".//footnote"):
            txt = (fn.text or "").lower()
            if "10b5-1" in txt:
                plan_flag = True
                break

    transactions: list[InsiderTransaction] = []
    # Non-derivative table holds direct stock buys/sells (the ones we care about).
    table = root.find("nonDerivativeTable")
    if table is None:
        return transactions

    for txn in table.findall("nonDerivativeTransaction"):
        try:
            coding = txn.find("transactionCoding")
            code = _value(coding, "transactionCode") if coding is not None else ""
            if code not in _VALID_CODES:
                # Unknown code - skip rather than guess.
                continue

            amounts = txn.find("transactionAmounts")
            shares = _to_int(_value(amounts, "transactionShares"))
            price = _to_float(_value(amounts, "transactionPricePerShare"))

            txn_d = _parse_date(_value(txn, "transactionDate"))
            if txn_d is None:
                continue

            # Per-transaction 10b5-1 flag override (v5 schema).
            txn_plan = plan_flag
            tcoding_plan = (
                coding.find("rule10b5-1Flag/value") if coding is not None else None
            )
            if tcoding_plan is not None and _text(tcoding_plan) in {"1", "true"}:
                txn_plan = True

            value = shares * price
            transactions.append(
                InsiderTransaction(
                    ticker=ticker,
                    filer=filer,
                    role=role,
                    txn_date=txn_d,
                    filing_date=filing_date,
                    transaction_code=code,  # type: ignore[arg-type]
                    shares=shares,
                    price_per_share=price,
                    value=value,
                    plan_flag=txn_plan,
                )
            )
        except (ValueError, AttributeError) as exc:
            logger.warning("skipping malformed Form 4 transaction: %s", exc)
            continue

    return transactions


# ----------------------------------------------------------------------------
# Atom feed (recent filings index).
# ----------------------------------------------------------------------------


def _parse_atom_entries(atom_bytes: bytes) -> list[tuple[str, date]]:
    """Return ``(filing_url, filing_date)`` tuples from EDGAR's atom feed."""
    out: list[tuple[str, date]] = []
    try:
        root = ET.fromstring(atom_bytes)  # noqa: S314
    except ET.ParseError as exc:
        logger.warning("EDGAR atom parse failed: %s", exc)
        return out

    for entry in root.findall("atom:entry", _ATOM_NS):
        link = entry.find("atom:link", _ATOM_NS)
        if link is None:
            continue
        href = link.get("href", "")
        if not href:
            continue
        updated = _text(entry.find("atom:updated", _ATOM_NS))
        # Atom updated is ISO 8601 with offset; trim to date.
        fdate = _parse_date(updated[:10]) or date.today()
        out.append((href, fdate))
    return out


def fetch_recent_form4(
    tickers: list[str] | None = None,
    days: int = 90,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[InsiderTransaction]:
    """Pull recent Form 4 filings.

    Parameters
    ----------
    tickers:
        If provided, only return transactions whose ticker matches (after
        upper-casing). ``None`` returns whatever the recent feed yielded.
    days:
        Maximum age (in days, by ``filing_date``) of filings to keep.
    user_agent:
        REQUIRED by SEC. Defaults to a research-tagged identifier that names
        the project owner so EDGAR can contact us if we misbehave.
    """
    if not user_agent or "@" not in user_agent:
        # SEC explicitly requires an email-tagged UA; refuse silently bad ones.
        raise ValueError(
            "SEC EDGAR requires a User-Agent containing a contact email."
        )

    # Stub-out alternate paths (per task spec).
    if os.environ.get("QUIVER_API_KEY"):
        return _fetch_via_quiver(tickers=tickers, days=days)
    if os.environ.get("OPENINSIDER_FALLBACK") == "true":
        return _fetch_via_openinsider(tickers=tickers, days=days)

    cutoff = date.today() - timedelta(days=days)
    filter_set: set[str] | None = (
        {t.upper() for t in tickers} if tickers else None
    )

    try:
        atom = _fetch_url(EDGAR_RECENT_FEED, user_agent=user_agent)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("EDGAR feed fetch failed: %s", exc)
        return []

    out: list[InsiderTransaction] = []
    for href, fdate in _parse_atom_entries(atom):
        if fdate < cutoff:
            continue
        try:
            xml_bytes = _fetch_url(href, user_agent=user_agent)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Form 4 fetch failed for %s: %s", href, exc)
            continue
        for txn in parse_form4_xml(xml_bytes, filing_date=fdate):
            if filter_set is not None and txn.ticker not in filter_set:
                continue
            out.append(txn)

    return out


def _fetch_via_quiver(
    tickers: list[str] | None, days: int
) -> list[InsiderTransaction]:
    """Stub for Quiver Quant insider endpoint - only flagged by env var."""
    raise NotImplementedError(
        "Quiver insider endpoint not implemented; remove QUIVER_API_KEY to use EDGAR."
    )


def _fetch_via_openinsider(
    tickers: list[str] | None, days: int
) -> list[InsiderTransaction]:
    """Stub for OpenInsider HTML scrape - only flagged by env var."""
    raise NotImplementedError(
        "OpenInsider scrape not implemented; unset OPENINSIDER_FALLBACK to use EDGAR."
    )


# ----------------------------------------------------------------------------
# Scoring.
# ----------------------------------------------------------------------------


def _clip01(x: float) -> float:
    return 0.0 if x <= 0 else (1.0 if x >= 1 else x)


def insider_buy_score(
    ticker: str,
    transactions: list[InsiderTransaction],
    asof: date,
    cluster_window_days: int = 5,
    cluster_min_insiders: int = 3,
    repeat_window_days: int = 90,
    repeat_min_buys: int = 3,
) -> float:
    """Score recent insider buying activity for ``ticker`` in ``[0, 1]``.

    Filters
    -------
    * ``transaction_code == 'P'`` (open-market purchases only).
    * ``plan_flag == False`` (10b5-1 planned trades excluded; they're scheduled
      and convey little information).
    * ``filing_date <= asof`` (look-ahead protection).
    * ``ticker`` match (case-insensitive).

    Subscores
    ---------
    * **cluster** (0..1): unique insiders buying inside ``cluster_window_days``
      of ``asof``, normalised against ``cluster_min_insiders``.
    * **repeat** (0..1): the most-active insider's buy-count inside
      ``repeat_window_days``, normalised against ``repeat_min_buys``.
    * **value** (0..1): aggregate purchase dollars over the cluster window,
      normalised against $1M (saturates above that).

    Returns
    -------
    ``0.5 * cluster + 0.3 * repeat + 0.2 * value``, clipped to ``[0, 1]``.
    """
    ticker_u = ticker.upper()
    cluster_start = asof - timedelta(days=cluster_window_days)
    repeat_start = asof - timedelta(days=repeat_window_days)

    # Single pass: filter once, partition into the two windows.
    cluster_filers: set[str] = set()
    cluster_value = 0.0
    repeat_counts: dict[str, int] = {}

    for t in transactions:
        if t.ticker != ticker_u:
            continue
        if t.transaction_code != "P":
            continue
        if t.plan_flag:
            continue
        if t.filing_date > asof:
            continue  # Look-ahead protection.

        if t.filing_date >= cluster_start:
            cluster_filers.add(t.filer)
            cluster_value += max(t.value, 0.0)
        if t.filing_date >= repeat_start:
            repeat_counts[t.filer] = repeat_counts.get(t.filer, 0) + 1

    if not cluster_filers and not repeat_counts:
        return 0.0

    cluster_score = _clip01(len(cluster_filers) / max(cluster_min_insiders, 1))
    max_buys = max(repeat_counts.values(), default=0)
    repeat_score = _clip01(max_buys / max(repeat_min_buys, 1))
    # $1M saturates the value subscore. This is a deliberately blunt scaler;
    # tune per-strategy if needed.
    value_score = _clip01(cluster_value / 1_000_000.0)

    score = 0.5 * cluster_score + 0.3 * repeat_score + 0.2 * value_score
    return _clip01(score)


__all__ = [
    "InsiderTransaction",
    "fetch_recent_form4",
    "insider_buy_score",
    "parse_form4_xml",
]
