"""Multi-agent dashboard API: graceful-empty-payload smoke tests.

Each test isolates JOURNAL_DIR + INCIDENTS_DIR + BACKTESTS_DIR to a tmp dir
so we exercise the no-data path without touching the real repo state.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    """TestClient with all filesystem state isolated to ``tmp_path``."""
    journal = tmp_path / "journal"
    journal.mkdir()
    incidents = tmp_path / "live" / "incidents"
    incidents.mkdir(parents=True)
    backtests = tmp_path / "backtests"
    backtests.mkdir()

    from dashboard.api import journal_reader, kill, multi_agent

    monkeypatch.setattr(journal_reader, "JOURNAL_DIR", journal)
    monkeypatch.setattr(multi_agent, "JOURNAL_DIR", journal)
    monkeypatch.setattr(multi_agent, "BACKTESTS_DIR", backtests)
    monkeypatch.setattr(kill, "INCIDENTS_DIR", incidents)

    # Reset state singleton between tests.
    from dashboard.api import state as state_module

    monkeypatch.setattr(state_module, "_state", None)

    # Defensive: kill any external alt-data env vars so /altdata/wallets
    # consistently returns its no-creds payload.
    monkeypatch.delenv("NANSEN_API_KEY", raising=False)

    from dashboard.api.main import app

    return TestClient(app)


# ----- /api/agents -----


def test_list_agents_returns_list(client: TestClient) -> None:
    r = client.get("/api/agents")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # Each entry shape, if any.
    for a in body:
        assert "name" in a
        assert "asset_class" in a
        assert "state" in a
        assert "heat_allocation" in a
        assert "n_open_positions" in a


# ----- /api/portfolio/equity -----


def test_portfolio_equity_no_data(client: TestClient) -> None:
    r = client.get("/api/portfolio/equity")
    assert r.status_code == 200
    body = r.json()
    assert body["points"] == []
    assert body["agent"] is None
    assert body["days"] == 90


def test_portfolio_equity_filter_by_agent(client: TestClient) -> None:
    r = client.get("/api/portfolio/equity", params={"agent": "equity_agent", "days": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["agent"] == "equity_agent"
    assert body["days"] == 30
    assert body["points"] == []


# ----- /api/positions/live -----


def test_positions_live_returns_200(client: TestClient) -> None:
    r = client.get("/api/positions/live")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_resolve_agent_for_symbol_covers_all_asset_classes() -> None:
    """The dashboard's symbol -> agent resolver attributes broker-reported
    positions back to the agent that owns the universe. Without this, every
    position rendered ``agent=null`` on /api/positions/live and per-agent
    dashboard stats stayed pinned at zero open positions.
    """
    from dashboard.api.multi_agent import _resolve_agent_for_symbol

    # Crypto (multi-shape: trading-API form, data-API form, internal USDT form).
    assert _resolve_agent_for_symbol("ETHUSD") == "crypto_agent"
    assert _resolve_agent_for_symbol("ETH/USD") == "crypto_agent"
    assert _resolve_agent_for_symbol("ETHUSDT") == "crypto_agent"
    assert _resolve_agent_for_symbol("BTCUSD") == "crypto_agent"
    assert _resolve_agent_for_symbol("DOGEUSDT") == "crypto_agent"
    # Specific buckets win over the generic equity catch-all.
    assert _resolve_agent_for_symbol("GLD") == "gold_agent"
    assert _resolve_agent_for_symbol("SLV") == "silver_agent"
    assert _resolve_agent_for_symbol("TLT") == "bonds_agent"
    # Equity catch-all.
    assert _resolve_agent_for_symbol("AAPL") == "equity_agent"
    assert _resolve_agent_for_symbol("SPY") == "equity_agent"
    # Unknown symbol -> None (don't fabricate attribution).
    assert _resolve_agent_for_symbol("THIS_TICKER_DOES_NOT_EXIST") is None
    assert _resolve_agent_for_symbol("") is None


# ----- /api/signals/recent -----


def test_signals_recent_empty(client: TestClient) -> None:
    r = client.get("/api/signals/recent")
    assert r.status_code == 200
    assert r.json() == []


def test_signals_recent_with_journal(client: TestClient, tmp_path: Path) -> None:
    """Drop a synthetic 'signal' event and verify it shows up."""
    from dashboard.api import journal_reader

    today = (
        journal_reader.JOURNAL_DIR
        / f"{datetime.now(UTC).date().strftime('%Y-%m-%d')}.jsonl"
    )
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "event": "signal",
        "agent": "equity_agent",
        "strategy": "mr_etf",
        "subject": "SPY",
        "side": "buy",
        "confidence": 0.81,
        "gap_filter": True,
        "news_filter": False,
    }
    today.write_text(json.dumps(payload) + "\n")

    r = client.get("/api/signals/recent")
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 1
    assert body[0]["agent"] == "equity_agent"
    assert body[0]["confidence"] == 0.81


# ----- /api/backtest/history -----


def test_backtest_history_unknown_strategy(client: TestClient) -> None:
    r = client.get("/api/backtest/history", params={"strategy": "does_not_exist"})
    assert r.status_code == 200
    assert r.json() == []


def test_backtest_history_with_runs(client: TestClient, tmp_path: Path) -> None:
    from dashboard.api import multi_agent

    run_dir = multi_agent.BACKTESTS_DIR / "mr_etf" / "20260421T142006Z"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        json.dumps({"sharpe": 1.36, "max_dd": 0.0047, "n_trades": 6})
    )

    r = client.get("/api/backtest/history", params={"strategy": "mr_etf", "days": 365})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["sharpe"] == 1.36
    assert body[0]["n_trades"] == 6


# ----- /api/coherence -----


def test_coherence_unknown_strategy(client: TestClient) -> None:
    r = client.get("/api/coherence", params={"strategy": "ghost"})
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"] == "ghost"
    assert body["coherence"] is None
    assert body["live_win_rate"] is None
    assert body["backtest_win_rate"] is None
    assert body["halted"] is False


# ----- /api/altdata/* -----


def test_altdata_insider_empty(client: TestClient) -> None:
    r = client.get("/api/altdata/insider", params={"ticker": "AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "AAPL"
    assert body["transactions"] == []
    assert body["score"] == 0.0


def test_altdata_sentiment_empty(client: TestClient) -> None:
    r = client.get("/api/altdata/sentiment", params={"ticker": "AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "AAPL"
    assert body["items"] == []
    assert body["rolling_score"] == 0.0


def test_altdata_wallets_no_key(client: TestClient) -> None:
    r = client.get("/api/altdata/wallets", params={"ticker": "BTC"})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "BTC"
    assert body["flows"] == []
    assert body["net_usd"] == 0.0
    assert "NANSEN_API_KEY" in (body.get("warning") or "")


# ----- /api/llm/governance -----


def test_llm_governance_returns_recommendations_list(client: TestClient) -> None:
    r = client.get("/api/llm/governance")
    assert r.status_code == 200
    body = r.json()
    assert "recommendations" in body
    assert isinstance(body["recommendations"], list)


# ----- /api/moonshot/status -----


def test_moonshot_status_has_four_lanes(client: TestClient) -> None:
    r = client.get("/api/moonshot/status")
    assert r.status_code == 200
    body = r.json()
    assert "lanes" in body
    names = {lane["name"] for lane in body["lanes"]}
    assert names == {
        "hft_sandbox",
        "aspirational_compounding",
        "copy_trading_shadow",
        "llm_discretionary_paper",
    }
    # Every lane has a status string and a metrics dict.
    for lane in body["lanes"]:
        assert "status" in lane
        assert isinstance(lane["metrics"], dict)
