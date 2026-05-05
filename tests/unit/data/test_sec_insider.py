"""Unit tests for SEC Form 4 ingestion + insider-buy scoring.

These tests must NEVER hit the network. The ``fetch_recent_form4`` test
monkeypatches ``_fetch_url`` so the only I/O is reading local fixture XML.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from src.data import sec_insider
from src.data.sec_insider import (
    InsiderTransaction,
    fetch_recent_form4,
    insider_buy_score,
    parse_form4_xml,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "form4"


# ----------------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------------


def _read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _txn(
    *,
    ticker: str = "ACME",
    filer: str = "INSIDER A",
    code: str = "P",
    filing_date: date,
    txn_date: date | None = None,
    shares: int = 100,
    price: float = 10.0,
    plan_flag: bool = False,
    role: str = "Director",
) -> InsiderTransaction:
    return InsiderTransaction(
        ticker=ticker,
        filer=filer,
        role=role,
        txn_date=txn_date or filing_date,
        filing_date=filing_date,
        transaction_code=code,  # type: ignore[arg-type]
        shares=shares,
        price_per_share=price,
        value=shares * price,
        plan_flag=plan_flag,
    )


# ----------------------------------------------------------------------------
# Parsing.
# ----------------------------------------------------------------------------


def test_parse_form4_xml_extracts_buy():
    txns = parse_form4_xml(_read_fixture("buy_simple.xml"), filing_date=date(2024, 8, 16))
    assert len(txns) == 1
    t = txns[0]
    assert t.ticker == "ACME"
    assert t.filer == "SMITH JOHN"
    assert "Director" in t.role and "Officer" in t.role
    assert t.transaction_code == "P"
    assert t.shares == 1000
    assert t.price_per_share == pytest.approx(50.25)
    assert t.value == pytest.approx(50250.0)
    assert t.plan_flag is False
    # txn_date is the trade date from the XML; filing_date is what we passed in.
    assert t.txn_date == date(2024, 8, 15)
    assert t.filing_date == date(2024, 8, 16)


def test_parse_form4_xml_preserves_filing_date_not_txn_date():
    """Critical for backtest look-ahead protection."""
    fdate = date(2024, 9, 1)
    txns = parse_form4_xml(_read_fixture("buy_simple.xml"), filing_date=fdate)
    assert txns[0].filing_date == fdate
    assert txns[0].txn_date != fdate


def test_parse_form4_xml_flags_10b51_plan():
    txns = parse_form4_xml(_read_fixture("plan_sell.xml"), filing_date=date(2024, 8, 21))
    assert len(txns) == 1
    assert txns[0].plan_flag is True
    assert txns[0].transaction_code == "S"


def test_parse_form4_xml_malformed_returns_empty_not_raises():
    # Garbage bytes must not crash the caller.
    out = parse_form4_xml(b"<not really xml", filing_date=date(2024, 1, 1))
    assert out == []


def test_parse_form4_xml_missing_tables_returns_empty():
    # Valid XML, but no nonDerivativeTable - should be empty, not crash.
    minimal = b"""<?xml version='1.0'?>
    <ownershipDocument>
      <documentType>4</documentType>
      <issuer><issuerTradingSymbol>FOO</issuerTradingSymbol></issuer>
    </ownershipDocument>"""
    assert parse_form4_xml(minimal, filing_date=date(2024, 1, 1)) == []


# ----------------------------------------------------------------------------
# Scoring.
# ----------------------------------------------------------------------------


def test_insider_buy_score_zero_with_no_transactions():
    assert insider_buy_score("ACME", [], date(2024, 8, 20)) == 0.0


def test_insider_buy_score_zero_when_only_other_ticker():
    txns = [_txn(ticker="OTHER", filer="X", filing_date=date(2024, 8, 19))]
    assert insider_buy_score("ACME", txns, date(2024, 8, 20)) == 0.0


def test_insider_buy_score_cluster_of_5_in_5_days_is_high():
    asof = date(2024, 8, 20)
    txns = [
        _txn(filer=f"INSIDER {i}", filing_date=asof, shares=100, price=10.0)
        for i in range(5)
    ]
    s = insider_buy_score("ACME", txns, asof, cluster_min_insiders=3)
    # 5 insiders / min 3 = saturates cluster (1.0). Cluster weight 0.5 alone
    # already pushes the floor to 0.5 - that's the "high" the task asks for.
    assert s >= 0.5
    assert s <= 1.0


def test_insider_buy_score_repeat_buyer_is_high():
    asof = date(2024, 8, 20)
    txns = [
        _txn(
            filer="INSIDER A",
            filing_date=date(2024, 8, 20 - i * 5),
            shares=100,
            price=10.0,
        )
        for i in range(3)
    ]
    s = insider_buy_score(
        "ACME", txns, asof, cluster_min_insiders=3, repeat_min_buys=3
    )
    # Same insider 3 times in 90d -> repeat_score saturates (1.0).
    # Cluster only sees 1 insider in last 5d -> cluster_score = 1/3.
    # Expect at least the 0.3 repeat weight.
    assert s >= 0.3


def test_insider_buy_score_filters_out_sells():
    asof = date(2024, 8, 20)
    txns = [
        _txn(filer=f"INSIDER {i}", code="S", filing_date=asof) for i in range(5)
    ]
    assert insider_buy_score("ACME", txns, asof) == 0.0


def test_insider_buy_score_filters_out_10b5_1_planned_sales_and_buys():
    asof = date(2024, 8, 20)
    txns = [
        _txn(filer=f"INSIDER {i}", code="P", plan_flag=True, filing_date=asof)
        for i in range(5)
    ]
    assert insider_buy_score("ACME", txns, asof) == 0.0


def test_insider_buy_score_excludes_filings_after_asof():
    """Look-ahead protection: future filings must not influence the score."""
    asof = date(2024, 8, 20)
    txns = [
        _txn(filer=f"INSIDER {i}", filing_date=date(2024, 8, 25))
        for i in range(5)
    ]
    assert insider_buy_score("ACME", txns, asof) == 0.0


def test_insider_buy_score_ranges_0_to_1():
    asof = date(2024, 8, 20)
    # Many insiders, many shares, big dollars - should still cap at 1.0.
    txns = [
        _txn(
            filer=f"INSIDER {i}",
            filing_date=asof,
            shares=100_000,
            price=100.0,
        )
        for i in range(20)
    ]
    s = insider_buy_score("ACME", txns, asof)
    assert 0.0 <= s <= 1.0


def test_insider_buy_score_combines_subscores():
    """Sanity: a strong cluster + strong repeat + big dollars should saturate."""
    from datetime import timedelta

    asof = date(2024, 8, 20)
    # 5 unique insiders today (cluster=1.0)
    cluster = [
        _txn(filer=f"NEW{i}", filing_date=asof, shares=2000, price=100.0)
        for i in range(5)
    ]
    # one of them has 2 more buys in the last 90d (repeat=1.0 with min=3)
    repeats = [
        _txn(filer="NEW0", filing_date=asof - timedelta(days=30 * (j + 1)), shares=100)
        for j in range(2)
    ]
    s = insider_buy_score("ACME", cluster + repeats, asof, repeat_min_buys=3)
    assert s == pytest.approx(1.0, abs=1e-6)


# ----------------------------------------------------------------------------
# Network seam (must NOT actually fetch).
# ----------------------------------------------------------------------------


def test_fetch_recent_form4_uses_seam_no_network(monkeypatch):
    feed_atom = b"""<?xml version='1.0'?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>4 - Acme Corp - SMITH JOHN</title>
        <link href="https://example.test/edgar/buy_simple.xml"/>
        <updated>2024-08-16T12:00:00-04:00</updated>
      </entry>
    </feed>"""
    buy_xml = _read_fixture("buy_simple.xml")

    calls: list[str] = []

    def fake_fetch(url: str, user_agent: str) -> bytes:
        calls.append(url)
        assert "@" in user_agent  # SEC requirement.
        if "browse-edgar" in url:
            return feed_atom
        if "buy_simple" in url:
            return buy_xml
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(sec_insider, "_fetch_url", fake_fetch)
    # Use a generous days window so the fixture's 2024 date isn't filtered.
    txns = fetch_recent_form4(tickers=["ACME"], days=100_000)
    assert len(txns) == 1
    assert txns[0].ticker == "ACME"
    assert calls[0].startswith("https://www.sec.gov/cgi-bin/browse-edgar")


def test_fetch_recent_form4_rejects_bad_user_agent(monkeypatch):
    # Should never get to the network with a UA missing an email.
    monkeypatch.setattr(
        sec_insider,
        "_fetch_url",
        lambda *a, **kw: pytest.fail("network attempted"),
    )
    with pytest.raises(ValueError):
        fetch_recent_form4(user_agent="anonymous")


def test_fetch_recent_form4_filters_by_ticker(monkeypatch):
    feed_atom = b"""<?xml version='1.0'?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <link href="https://example.test/edgar/buy_simple.xml"/>
        <updated>2024-08-16T12:00:00-04:00</updated>
      </entry>
    </feed>"""
    buy_xml = _read_fixture("buy_simple.xml")

    def fake_fetch(url: str, user_agent: str) -> bytes:
        if "browse-edgar" in url:
            return feed_atom
        return buy_xml

    monkeypatch.setattr(sec_insider, "_fetch_url", fake_fetch)
    out = fetch_recent_form4(tickers=["NOT_ACME"], days=100_000)
    assert out == []


def test_fetch_recent_form4_quiver_stub_raises_when_env_set(monkeypatch):
    monkeypatch.setenv("QUIVER_API_KEY", "fake")
    monkeypatch.setattr(
        sec_insider,
        "_fetch_url",
        lambda *a, **kw: pytest.fail("network attempted"),
    )
    with pytest.raises(NotImplementedError):
        fetch_recent_form4()


def test_fetch_recent_form4_openinsider_stub_raises_when_env_set(monkeypatch):
    monkeypatch.setenv("OPENINSIDER_FALLBACK", "true")
    monkeypatch.setattr(
        sec_insider,
        "_fetch_url",
        lambda *a, **kw: pytest.fail("network attempted"),
    )
    with pytest.raises(NotImplementedError):
        fetch_recent_form4()
