"""Unit tests for crypto smart-money wallet ingestion + acceptance gating.

These tests must NEVER hit the network. The only network seam in the module
is ``_fetch_url``; we monkeypatch it where needed and rely on env-var gating
for the no-API-key path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from src.data import crypto_wallets
from src.data.crypto_wallets import (
    WalletTrade,
    evaluate_wallet,
    fetch_smart_money_trades,
)

# ----------------------------------------------------------------------------
# Helpers — synthetic WalletTrade fixtures.
# ----------------------------------------------------------------------------


def _trade(
    *,
    address: str = "0xabc",
    label: str = "Nansen smart money",
    ticker: str = "ETH",
    side: str = "buy",
    txn_time: datetime,
    observed_price: float = 100.0,
    slippage_bps: float | None = 5.0,
    fill: float | None = None,
) -> WalletTrade:
    return WalletTrade(
        wallet_address=address,
        label=label,
        ticker=ticker,
        side=side,  # type: ignore[arg-type]
        txn_time=txn_time,
        observed_price=observed_price,
        our_simulated_fill=fill,
        our_simulated_slippage_bps=slippage_bps,
    )


def _flat_pnl_lookup(_ticker: str, _ts: datetime) -> float:
    """Returns 110.0 for every (ticker, ts) — every buy at 100.0 is +10."""
    return 110.0


def _build_history(
    *,
    n: int,
    asof: datetime,
    ticker_for: callable = lambda i: "ETH",
    side_for: callable = lambda i: "buy",
    price_for: callable = lambda i: 100.0,
    slip_for: callable = lambda i: 5.0,
) -> list[WalletTrade]:
    """Build ``n`` trades spread across the trailing 200 days for persistence.

    By default everything is buys @ 100 with 5 bps slippage on ETH; tests
    override individual lambdas to perturb specific dimensions.
    """
    history: list[WalletTrade] = []
    # Spread trades roughly evenly through the 200-day window to ensure the
    # 30/90/180-day persistence checks all see at least some activity.
    for i in range(n):
        days_back = (i % 180) + 1  # 1..180 inclusive
        history.append(
            _trade(
                ticker=ticker_for(i),
                side=side_for(i),
                observed_price=price_for(i),
                slippage_bps=slip_for(i),
                txn_time=asof - timedelta(days=days_back),
            )
        )
    return history


# ----------------------------------------------------------------------------
# fetch_smart_money_trades — no API key path.
# ----------------------------------------------------------------------------


def test_fetch_smart_money_trades_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("NANSEN_API_KEY", raising=False)
    monkeypatch.setattr(
        crypto_wallets,
        "_fetch_url",
        lambda *a, **kw: pytest.fail("network attempted without API key"),
    )
    assert fetch_smart_money_trades(chains=["ethereum"], hours=168) == []


def test_fetch_smart_money_trades_no_chains_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("NANSEN_API_KEY", raising=False)
    assert fetch_smart_money_trades(chains=None, hours=168) == []


def test_fetch_smart_money_trades_with_key_uses_seam(monkeypatch):
    now = datetime.now(tz=UTC)
    payload = json.dumps(
        [
            {
                "address": "0xdeadbeef",
                "symbol": "ETH",
                "side": "buy",
                "blockTimestamp": (now - timedelta(hours=1)).isoformat(),
                "priceUsd": 3500.0,
                "label": "Nansen smart money",
            }
        ]
    ).encode("utf-8")

    calls: list[str] = []

    def fake_fetch(url: str, api_key: str) -> bytes:
        calls.append(url)
        assert api_key == "fake-key"
        return payload

    monkeypatch.setattr(crypto_wallets, "_fetch_url", fake_fetch)
    out = fetch_smart_money_trades(
        chains=["ethereum"], hours=168, api_key="fake-key"
    )
    assert len(out) == 1
    assert out[0].wallet_address == "0xdeadbeef"
    assert out[0].ticker == "ETH"
    assert out[0].side == "buy"
    assert out[0].observed_price == 3500.0
    assert calls and "ethereum" in calls[0]


def test_fetch_smart_money_trades_uses_env_var(monkeypatch):
    monkeypatch.setenv("NANSEN_API_KEY", "env-key")
    captured: dict[str, str] = {}

    def fake_fetch(url: str, api_key: str) -> bytes:
        captured["api_key"] = api_key
        return b"[]"

    monkeypatch.setattr(crypto_wallets, "_fetch_url", fake_fetch)
    fetch_smart_money_trades(chains=["ethereum"], hours=168)
    assert captured["api_key"] == "env-key"


def test_fetch_smart_money_trades_handles_object_payload(monkeypatch):
    """Nansen sometimes wraps the array in ``{"data": [...]}``."""
    now = datetime.now(tz=UTC)
    payload = json.dumps(
        {
            "data": [
                {
                    "address": "0xa",
                    "symbol": "SOL",
                    "side": "sell",
                    "blockTimestamp": (now - timedelta(hours=2)).isoformat(),
                    "priceUsd": 150.0,
                }
            ]
        }
    ).encode("utf-8")
    monkeypatch.setattr(
        crypto_wallets, "_fetch_url", lambda url, api_key: payload
    )
    out = fetch_smart_money_trades(
        chains=["solana"], hours=168, api_key="x"
    )
    assert len(out) == 1
    assert out[0].ticker == "SOL"
    assert out[0].side == "sell"


def test_fetch_smart_money_trades_filters_old_trades(monkeypatch):
    now = datetime.now(tz=UTC)
    payload = json.dumps(
        [
            {
                "address": "0xa",
                "symbol": "ETH",
                "side": "buy",
                # Older than 168h cutoff.
                "blockTimestamp": (now - timedelta(hours=200)).isoformat(),
                "priceUsd": 100.0,
            },
            {
                "address": "0xb",
                "symbol": "ETH",
                "side": "buy",
                "blockTimestamp": (now - timedelta(hours=10)).isoformat(),
                "priceUsd": 100.0,
            },
        ]
    ).encode("utf-8")
    monkeypatch.setattr(
        crypto_wallets, "_fetch_url", lambda url, api_key: payload
    )
    out = fetch_smart_money_trades(
        chains=["ethereum"], hours=168, api_key="x"
    )
    assert len(out) == 1
    assert out[0].wallet_address == "0xb"


def test_fetch_smart_money_trades_network_error_returns_empty(monkeypatch):
    import urllib.error

    def fake_fetch(url: str, api_key: str) -> bytes:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(crypto_wallets, "_fetch_url", fake_fetch)
    out = fetch_smart_money_trades(
        chains=["ethereum"], hours=168, api_key="x"
    )
    assert out == []


# ----------------------------------------------------------------------------
# evaluate_wallet — acceptance criteria.
# ----------------------------------------------------------------------------


def test_evaluate_wallet_49_trades_rejected_for_min_count():
    asof = datetime(2025, 6, 1, tzinfo=UTC)
    history = _build_history(n=49, asof=asof)
    result = evaluate_wallet("0xabc", history, _flat_pnl_lookup)
    assert result.accepted is False
    assert result.n_trades == 49
    # Reason is observable via the lower-than-min count; the result captures
    # the supporting fields so callers can build their own reason string.


def test_evaluate_wallet_50_clean_profile_accepted():
    asof = datetime(2025, 6, 1, tzinfo=UTC)
    # 50 buys @ 100; lookup returns 110 → every position is +10 (positive P&L
    # in every trailing window). Spread across many tokens → low concentration.
    history = _build_history(
        n=50,
        asof=asof,
        ticker_for=lambda i: f"TOK{i % 10}",  # 10 unique tokens
        slip_for=lambda i: 5.0,
    )
    result = evaluate_wallet("0xabc", history, _flat_pnl_lookup)
    assert result.n_trades == 50
    assert result.persistence_30d
    assert result.persistence_90d
    assert result.persistence_180d
    assert result.pnl_concentration <= 0.40
    assert result.avg_slippage_bps < 25.0
    assert result.accepted is True


def test_evaluate_wallet_60_trades_high_concentration_rejected():
    """60 trades but 50% of P&L from a single token → reject for concentration."""
    asof = datetime(2025, 6, 1, tzinfo=UTC)

    # 60 trades across 5 tokens but TOK0 carries 50% of the absolute P&L.
    # Strategy: TOK0 is bought at 100 with mark 200 (+100 each, 30 of these),
    # and the other 4 tokens are bought at 100 with mark 110 (+10 each, 30 trades).
    # Total |P&L|: TOK0 = 30*100 = 3000; others = 30*10 = 300. → 3000/3300 = 0.909
    history: list[WalletTrade] = []
    for i in range(30):
        history.append(
            _trade(
                ticker="TOK0",
                txn_time=asof - timedelta(days=(i % 180) + 1),
                observed_price=100.0,
                slippage_bps=5.0,
            )
        )
    for i in range(30):
        history.append(
            _trade(
                ticker=f"TOK{(i % 4) + 1}",
                txn_time=asof - timedelta(days=(i % 180) + 1),
                observed_price=100.0,
                slippage_bps=5.0,
            )
        )

    def lookup(ticker: str, _ts: datetime) -> float:
        # TOK0 doubles; everything else +10.
        return 200.0 if ticker == "TOK0" else 110.0

    result = evaluate_wallet("0xabc", history, lookup)
    assert result.n_trades == 60
    assert result.pnl_concentration > 0.40
    assert result.accepted is False


def test_evaluate_wallet_high_slippage_rejected():
    asof = datetime(2025, 6, 1, tzinfo=UTC)
    # 60 trades, well-diversified, positive P&L — but slippage is 30bps avg.
    history = _build_history(
        n=60,
        asof=asof,
        ticker_for=lambda i: f"TOK{i % 10}",
        slip_for=lambda i: 30.0,
    )
    result = evaluate_wallet("0xabc", history, _flat_pnl_lookup)
    assert result.avg_slippage_bps == pytest.approx(30.0)
    assert result.avg_slippage_bps >= 25.0
    assert result.accepted is False


def test_evaluate_wallet_empty_history_rejected():
    result = evaluate_wallet("0xabc", [], _flat_pnl_lookup)
    assert result.n_trades == 0
    assert result.accepted is False


def test_evaluate_wallet_negative_persistence_rejected():
    """If P&L is negative, persistence flags are False → reject."""
    asof = datetime(2025, 6, 1, tzinfo=UTC)
    history = _build_history(
        n=60,
        asof=asof,
        ticker_for=lambda i: f"TOK{i % 10}",
    )

    def losing_lookup(_ticker: str, _ts: datetime) -> float:
        return 90.0  # below the 100.0 entry → every buy is -10

    result = evaluate_wallet("0xabc", history, losing_lookup)
    assert result.persistence_30d is False
    assert result.persistence_90d is False
    assert result.persistence_180d is False
    assert result.accepted is False
