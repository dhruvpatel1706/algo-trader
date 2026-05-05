"""Unit tests for Congressional trading ingestion + watchlist boost.

These tests must NEVER hit the network. The only network seam in the module
is ``_fetch_url``; we monkeypatch it where needed and rely on env-var gating
for the no-API-key path.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from src.data import congress
from src.data.congress import (
    CongressTrade,
    fetch_congress_trades,
    watchlist_boost,
)

# ----------------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------------


def _trade(
    *,
    ticker: str = "ACME",
    member: str = "REP A",
    party: str = "D",
    chamber: str = "house",
    committee: str | None = "Finance",
    txn_date: date | None = None,
    filing_date: date | None = None,
    amount_low: float = 1_001.0,
    amount_high: float = 15_000.0,
    type_: str = "P",
) -> CongressTrade:
    # Default both to today; if only one is supplied, mirror it to the other.
    if txn_date is None and filing_date is None:
        txn_date = filing_date = date(2025, 1, 1)
    elif txn_date is None:
        txn_date = filing_date
    elif filing_date is None:
        filing_date = txn_date
    return CongressTrade(
        ticker=ticker,
        member=member,
        party=party,  # type: ignore[arg-type]
        chamber=chamber,  # type: ignore[arg-type]
        committee=committee,
        txn_date=txn_date,  # type: ignore[arg-type]
        filing_date=filing_date,  # type: ignore[arg-type]
        amount_low=amount_low,
        amount_high=amount_high,
        type=type_,  # type: ignore[arg-type]
    )


# ----------------------------------------------------------------------------
# fetch_congress_trades — no API key path.
# ----------------------------------------------------------------------------


def test_fetch_congress_trades_no_api_key_returns_empty(monkeypatch):
    """No env var, no explicit key → empty list, no crash."""
    monkeypatch.delenv("QUIVER_API_KEY", raising=False)
    # If anything tried the network it would fail the test outright.
    monkeypatch.setattr(
        congress,
        "_fetch_url",
        lambda *a, **kw: pytest.fail("network attempted without API key"),
    )
    out = fetch_congress_trades(tickers=["ACME"], days=90)
    assert out == []


def test_fetch_congress_trades_no_api_key_no_tickers_returns_empty(monkeypatch):
    """Even with no tickers and no key, must not crash."""
    monkeypatch.delenv("QUIVER_API_KEY", raising=False)
    assert fetch_congress_trades(tickers=None, days=90) == []
    assert fetch_congress_trades(tickers=[], days=90) == []


def test_fetch_congress_trades_with_key_uses_quiver_seam(monkeypatch):
    """Explicit api_key triggers the Quiver path; we mock the fetch."""
    payload = json.dumps(
        [
            {
                "Ticker": "ACME",
                "Representative": "Jane Doe",
                "Party": "D",
                "Chamber": "house",
                "Committee": "Finance",
                "Transaction": "Purchase",
                "TransactionDate": (date.today() - timedelta(days=5)).isoformat(),
                "ReportDate": (date.today() - timedelta(days=2)).isoformat(),
                "Range": "$1,001 - $15,000",
            }
        ]
    ).encode("utf-8")

    calls: list[str] = []

    def fake_fetch(url: str, api_key: str) -> bytes:
        calls.append(url)
        assert api_key == "fake-key"
        return payload

    monkeypatch.setattr(congress, "_fetch_url", fake_fetch)
    out = fetch_congress_trades(tickers=["ACME"], days=90, api_key="fake-key")
    assert len(out) == 1
    assert out[0].ticker == "ACME"
    assert out[0].member == "Jane Doe"
    assert out[0].type == "P"
    assert out[0].amount_low == 1001.0
    assert out[0].amount_high == 15000.0
    assert calls and "ACME" in calls[0]


def test_fetch_congress_trades_uses_env_var_when_no_explicit_key(monkeypatch):
    monkeypatch.setenv("QUIVER_API_KEY", "env-key")
    captured: dict[str, str] = {}

    def fake_fetch(url: str, api_key: str) -> bytes:
        captured["api_key"] = api_key
        return b"[]"

    monkeypatch.setattr(congress, "_fetch_url", fake_fetch)
    fetch_congress_trades(tickers=["ACME"], days=90)
    assert captured["api_key"] == "env-key"


def test_fetch_congress_trades_filters_by_filing_date_cutoff(monkeypatch):
    """Trades older than ``days`` (by filing_date) are dropped."""
    old = (date.today() - timedelta(days=200)).isoformat()
    fresh = (date.today() - timedelta(days=10)).isoformat()
    payload = json.dumps(
        [
            {
                "Ticker": "ACME",
                "Representative": "Old Member",
                "Transaction": "Purchase",
                "TransactionDate": old,
                "ReportDate": old,
                "Range": "$1,001 - $15,000",
            },
            {
                "Ticker": "ACME",
                "Representative": "Fresh Member",
                "Transaction": "Purchase",
                "TransactionDate": fresh,
                "ReportDate": fresh,
                "Range": "$1,001 - $15,000",
            },
        ]
    ).encode("utf-8")

    monkeypatch.setattr(congress, "_fetch_url", lambda url, api_key: payload)
    out = fetch_congress_trades(tickers=["ACME"], days=90, api_key="x")
    assert len(out) == 1
    assert out[0].member == "Fresh Member"


def test_fetch_congress_trades_network_error_returns_empty(monkeypatch):
    """A failed fetch must not crash callers; empty result instead."""
    import urllib.error

    def fake_fetch(url: str, api_key: str) -> bytes:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(congress, "_fetch_url", fake_fetch)
    out = fetch_congress_trades(tickers=["ACME"], days=90, api_key="x")
    assert out == []


def test_fetch_congress_trades_garbage_payload_returns_empty(monkeypatch):
    monkeypatch.setattr(congress, "_fetch_url", lambda *a, **kw: b"not json")
    out = fetch_congress_trades(tickers=["ACME"], days=90, api_key="x")
    assert out == []


# ----------------------------------------------------------------------------
# watchlist_boost — scoring behaviour.
# ----------------------------------------------------------------------------


def test_watchlist_boost_no_trades_is_zero():
    assert watchlist_boost("ACME", [], date(2025, 1, 1)) == 0.0


def test_watchlist_boost_only_other_ticker_is_zero():
    asof = date(2025, 1, 15)
    trades = [_trade(ticker="OTHER", filing_date=asof) for _ in range(5)]
    assert watchlist_boost("ACME", trades, asof) == 0.0


def test_watchlist_boost_5_buys_30_days_different_members_is_high():
    """5 different-member buys in last 30 days saturates cluster (cluster_min=3)."""
    asof = date(2025, 1, 31)
    trades = [
        _trade(
            member=f"REP {i}",
            txn_date=asof - timedelta(days=2),
            filing_date=asof - timedelta(days=1),
            amount_low=15_000.0,
            amount_high=50_000.0,
        )
        for i in range(5)
    ]
    score = watchlist_boost("ACME", trades, asof, cluster_min=3)
    # Cluster saturates (1.0) → 0.5 weight alone yields >= 0.5; with $75K of
    # value there's also a small value contribution.
    assert score >= 0.5
    assert score <= 1.0


def test_watchlist_boost_all_sales_is_zero():
    asof = date(2025, 1, 31)
    trades = [
        _trade(member=f"REP {i}", filing_date=asof, type_="S") for i in range(5)
    ]
    assert watchlist_boost("ACME", trades, asof) == 0.0


def test_watchlist_boost_late_filer_adds_small_boost():
    """Late filers (filing > 45 days after txn) get a small positive bump."""
    asof = date(2025, 6, 1)
    # Filed 1 day ago, but underlying trade was 60 days ago → late filer.
    late_trade = _trade(
        member="REP LATE",
        txn_date=asof - timedelta(days=60),
        filing_date=asof - timedelta(days=1),
    )
    timely_trade = _trade(
        member="REP TIMELY",
        txn_date=asof - timedelta(days=2),
        filing_date=asof - timedelta(days=1),
    )

    s_with_late = watchlist_boost("ACME", [late_trade], asof, cluster_min=3)
    s_timely = watchlist_boost("ACME", [timely_trade], asof, cluster_min=3)
    # Both have one buyer → identical cluster contribution; late_filer adds.
    assert s_with_late > s_timely
    # The bump is bounded by the 0.15 late-filer weight.
    assert s_with_late - s_timely == pytest.approx(0.15, abs=1e-6)


def test_watchlist_boost_excludes_filings_after_asof():
    """Look-ahead protection: a filing dated after asof must not contribute."""
    asof = date(2025, 1, 15)
    future_trades = [
        _trade(
            member=f"REP {i}",
            txn_date=asof - timedelta(days=2),
            filing_date=asof + timedelta(days=5),  # in the future
        )
        for i in range(5)
    ]
    assert watchlist_boost("ACME", future_trades, asof) == 0.0


def test_watchlist_boost_clipped_to_unit_interval():
    asof = date(2025, 1, 31)
    trades = [
        _trade(
            member=f"REP {i}",
            txn_date=asof - timedelta(days=60),
            filing_date=asof - timedelta(days=1),
            amount_low=1_000_000.0,  # way above $500K saturation
        )
        for i in range(20)
    ]
    score = watchlist_boost("ACME", trades, asof, cluster_min=3)
    assert 0.0 <= score <= 1.0
