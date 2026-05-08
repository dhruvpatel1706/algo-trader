"""Tests for src.agents.alt_data_multiplier.

The alt-data multiplier composes three independent equity-only signals
(insider Form 4 cluster, Quiver Congress watchlist, Finnhub news
sentiment) into a clamped [0.7, 1.3] multiplier. The trade pipeline
applies it alongside the analyst and reasoner multipliers.

Test contract:
  - Returns 1.0 (passthrough) for non-equity asset classes.
  - Returns 1.0 when no fetchers are wired (degrades gracefully).
  - Returns 1.0 when the symbol is unknown to all sources.
  - Boosts above 1.0 when at least one source contributes positively.
  - Dampens below 1.0 when news sentiment is strongly bearish.
  - Clamps to [0.7, 1.3] regardless of how many sources fire.
  - Caches results per (source, symbol) for the TTL window.
  - Degrades to neutral on any fetcher exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from src.agents.alt_data_multiplier import (
    AltDataVerdict,
    compute_alt_data_multiplier,
    reset_alt_cache,
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Tests must not see each other's cached fetcher results."""
    reset_alt_cache()
    yield
    reset_alt_cache()


@dataclass
class _Txn:
    """Minimal stand-in for src.data.sec_insider.InsiderTransaction."""

    filer: str
    transaction_code: str
    filing_date: date


def test_non_equity_asset_class_passes_through_at_1():
    """Crypto / bonds / gold / silver have no Form 4 / Congress / company news."""
    v = compute_alt_data_multiplier("BTCUSDT", "buy", asset_class="crypto")
    assert v.multiplier == 1.0
    assert "crypto" in v.reasoning


def test_short_signal_passes_through_at_1():
    """The bot is long-only today; defence against future ambiguity by
    neutralising the multiplier on shorts so a side-flip bug can't
    accidentally flip the sign of the boost."""
    v = compute_alt_data_multiplier("AAPL", "sell", asset_class="equity")
    assert v.multiplier == 1.0
    assert "sell" in v.reasoning.lower()


def test_no_fetchers_wired_returns_neutral():
    """Default path: caller didn't supply any fetchers — degrade silently."""
    v = compute_alt_data_multiplier("AAPL", "buy", asset_class="equity")
    assert v.multiplier == 1.0
    assert v.contributions == {}


def test_three_insider_cluster_buys_boost_multiplier(monkeypatch):
    """Threshold case: exactly 3 unique buyers in the last 14 days fires
    the +0.10 cluster boost."""
    asof = date(2026, 5, 7)
    txns = [
        _Txn(filer=f"insider_{i}", transaction_code="P", filing_date=asof - timedelta(days=2 + i))
        for i in range(3)
    ]
    fetcher = lambda sym, d: txns  # noqa: E731

    v = compute_alt_data_multiplier(
        "AAPL",
        "buy",
        asset_class="equity",
        asof=asof,
        insider_fetcher=fetcher,
    )
    assert v.multiplier > 1.0
    assert v.n_insider_buys == 3
    assert "insider" in v.contributions


def test_two_insider_buys_below_threshold_does_not_fire():
    """Two buyers is below the cluster threshold of 3 — no boost.
    Avoids over-boosting on co-founder coordinated buys."""
    asof = date(2026, 5, 7)
    txns = [
        _Txn(filer="a", transaction_code="P", filing_date=asof),
        _Txn(filer="b", transaction_code="P", filing_date=asof),
    ]
    v = compute_alt_data_multiplier(
        "AAPL",
        "buy",
        asset_class="equity",
        asof=asof,
        insider_fetcher=lambda s, d: txns,
    )
    assert v.multiplier == 1.0


def test_insider_sales_are_ignored():
    """Sales (transaction_code='S') do not count for the cluster signal —
    too noisy due to 10b5-1 plans / vesting / scheduled divestiture."""
    asof = date(2026, 5, 7)
    txns = [
        _Txn(filer=f"insider_{i}", transaction_code="S", filing_date=asof)
        for i in range(5)
    ]
    v = compute_alt_data_multiplier(
        "AAPL",
        "buy",
        asset_class="equity",
        asof=asof,
        insider_fetcher=lambda s, d: txns,
    )
    assert v.multiplier == 1.0


def test_filings_after_asof_excluded_for_lookahead_protection():
    """Backtests must not see filings dated after the evaluation date."""
    asof = date(2026, 5, 7)
    txns = [
        _Txn(filer=f"i_{n}", transaction_code="P", filing_date=asof + timedelta(days=n))
        for n in range(3)
    ]
    v = compute_alt_data_multiplier(
        "AAPL",
        "buy",
        asset_class="equity",
        asof=asof,
        insider_fetcher=lambda s, d: txns,
    )
    # All three are AFTER asof — must be excluded.
    assert v.multiplier == 1.0


def test_congress_watchlist_above_05_contributes():
    """Quiver watchlist boost above 0.5 contributes a small (+0.05) lift."""
    v = compute_alt_data_multiplier(
        "MSFT",
        "buy",
        asset_class="equity",
        congress_fetcher=lambda s, d: 1.0,
    )
    assert v.multiplier > 1.0
    assert "congress" in v.contributions


def test_congress_watchlist_below_05_does_not_fire():
    """Sub-threshold Congress signal stays neutral (avoids noisy fluctuations)."""
    v = compute_alt_data_multiplier(
        "MSFT",
        "buy",
        asset_class="equity",
        congress_fetcher=lambda s, d: 0.4,
    )
    assert v.multiplier == 1.0


def test_news_strongly_positive_boosts_multiplier():
    v = compute_alt_data_multiplier(
        "NVDA",
        "buy",
        asset_class="equity",
        news_sentiment_fetcher=lambda s, since: [0.8, 0.9, 0.7],
    )
    assert v.multiplier > 1.0
    assert "news_bull" in v.contributions
    assert v.n_news_articles == 3


def test_news_strongly_negative_dampens_multiplier():
    v = compute_alt_data_multiplier(
        "NVDA",
        "buy",
        asset_class="equity",
        news_sentiment_fetcher=lambda s, since: [-0.8, -0.9],
    )
    assert v.multiplier < 1.0
    assert "news_bear" in v.contributions


def test_neutral_news_does_not_change_multiplier():
    """News scoring near 0 should not move the multiplier — avoid amplifying
    statistical noise on routine wire-service prints."""
    v = compute_alt_data_multiplier(
        "NVDA",
        "buy",
        asset_class="equity",
        news_sentiment_fetcher=lambda s, since: [0.1, -0.05, 0.0],
    )
    assert v.multiplier == 1.0


def test_multiplier_is_clamped_to_ceiling_when_all_sources_fire():
    """All three sources max → composite stays at the ceiling 1.30, not
    an unbounded sum."""
    asof = date(2026, 5, 7)
    big_cluster = [
        _Txn(filer=f"i_{n}", transaction_code="P", filing_date=asof)
        for n in range(20)
    ]
    v = compute_alt_data_multiplier(
        "AAPL",
        "buy",
        asset_class="equity",
        asof=asof,
        insider_fetcher=lambda s, d: big_cluster,
        congress_fetcher=lambda s, d: 1.0,
        news_sentiment_fetcher=lambda s, since: [0.95, 0.95],
    )
    assert v.multiplier == 1.3, f"expected ceiling clamp, got {v.multiplier}"


def test_fetcher_exceptions_degrade_to_neutral():
    """A fetcher that raises must NOT abort the verdict — degrade
    gracefully so an upstream API outage doesn't kill the whole pipeline."""
    def boom(*args, **kwargs):
        raise RuntimeError("upstream 503")

    v = compute_alt_data_multiplier(
        "AAPL",
        "buy",
        asset_class="equity",
        insider_fetcher=boom,
        congress_fetcher=boom,
        news_sentiment_fetcher=boom,
    )
    assert v.multiplier == 1.0


def test_cache_serves_repeat_calls_within_ttl():
    """Two evaluations of the same symbol within the TTL window must
    only call each fetcher ONCE."""
    asof = date(2026, 5, 7)
    insider_calls = []

    def insider_fetcher(sym, d):
        insider_calls.append(sym)
        return [
            _Txn(filer=f"i_{n}", transaction_code="P", filing_date=asof)
            for n in range(3)
        ]

    compute_alt_data_multiplier(
        "AAPL", "buy", asset_class="equity", asof=asof, insider_fetcher=insider_fetcher
    )
    compute_alt_data_multiplier(
        "AAPL", "buy", asset_class="equity", asof=asof, insider_fetcher=insider_fetcher
    )
    assert len(insider_calls) == 1, (
        "second call must hit cache, not re-call the fetcher"
    )


def test_verdict_is_journal_safe():
    """AltDataVerdict must serialise into primitive types so the journal
    writer can append it without custom encoders."""
    v = compute_alt_data_multiplier("AAPL", "buy", asset_class="equity")
    assert isinstance(v, AltDataVerdict)
    assert isinstance(v.multiplier, float)
    assert isinstance(v.reasoning, str)
    assert isinstance(v.contributions, dict)
    # All contributions values are floats.
    for k, val in v.contributions.items():
        assert isinstance(k, str)
        assert isinstance(val, float)
