"""Tests for the Alpaca portfolio_history fallback in /api/portfolio/equity.

When the journal-derived equity curve is empty (e.g. brand-new paper account
with no trades yet), the dashboard falls back to Alpaca's server-side daily
equity snapshots so the chart shows a real flat line instead of an empty
state. As soon as the bot trades, the journal-derived path takes over.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    """TestClient with all filesystem state isolated to tmp_path."""
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

    from dashboard.api import state as state_module

    monkeypatch.setattr(state_module, "_state", None)

    # Reset broker proxy singleton so each test gets a clean slate.
    from dashboard.api import broker_proxy as broker_proxy_module

    monkeypatch.setattr(broker_proxy_module, "_proxy", None)

    from dashboard.api.main import app

    return TestClient(app)


def _stub_history_points() -> list[dict]:
    return [
        {"ts": "2026-04-01T00:00:00+00:00", "equity": 100000.0},
        {"ts": "2026-04-02T00:00:00+00:00", "equity": 100050.0},
        {"ts": "2026-04-03T00:00:00+00:00", "equity": 100100.0},
    ]


# ---------------------------------------------------------------------------
# Endpoint behaviour.
# ---------------------------------------------------------------------------


def test_portfolio_history_returns_alpaca_data_when_journal_empty(
    client: TestClient, monkeypatch
) -> None:
    """No journal fills + Alpaca history available → endpoint returns Alpaca points."""
    from dashboard.api import broker_proxy as broker_proxy_module

    points = _stub_history_points()

    def fake_get(self: Any, *, days: int = 90) -> list[dict]:
        return list(points)

    monkeypatch.setattr(
        broker_proxy_module.BrokerProxy, "get_portfolio_history", fake_get
    )

    r = client.get("/api/portfolio/equity")
    assert r.status_code == 200
    body = r.json()
    assert len(body["points"]) == 3
    assert body["points"][0]["equity"] == 100000.0
    assert body["points"][-1]["equity"] == 100100.0
    assert body["start_equity"] == 100000.0
    assert body["end_equity"] == 100100.0


def test_portfolio_history_uses_journal_when_journal_has_fills(
    client: TestClient, monkeypatch
) -> None:
    """Journal fills exist → journal path wins; Alpaca fallback is NOT used."""
    from dashboard.api import broker_proxy as broker_proxy_module
    from dashboard.api import journal_reader

    today = datetime.now(UTC).date()
    today_path = journal_reader.JOURNAL_DIR / f"{today.strftime('%Y-%m-%d')}.jsonl"
    yesterday = today.replace(day=max(1, today.day - 1)) if today.day > 1 else today
    y_path = journal_reader.JOURNAL_DIR / f"{yesterday.strftime('%Y-%m-%d')}.jsonl"
    fills = [
        {
            "ts": f"{yesterday.isoformat()}T10:00:00+00:00",
            "event": "fill",
            "agent": "equity_agent",
            "pnl": 50.0,
        },
        {
            "ts": f"{today.isoformat()}T10:00:00+00:00",
            "event": "fill",
            "agent": "equity_agent",
            "pnl": -10.0,
        },
    ]
    if today != yesterday:
        y_path.write_text(json.dumps(fills[0]) + "\n")
        today_path.write_text(json.dumps(fills[1]) + "\n")
    else:
        today_path.write_text(
            "\n".join(json.dumps(f) for f in fills) + "\n"
        )

    # Stub a 5-point Alpaca history that would win if journal were empty.
    alpaca_called = False

    def fake_get(self: Any, *, days: int = 90) -> list[dict]:
        nonlocal alpaca_called
        alpaca_called = True
        return [
            {"ts": "2026-04-01T00:00:00+00:00", "equity": 1.0},
            {"ts": "2026-04-02T00:00:00+00:00", "equity": 2.0},
            {"ts": "2026-04-03T00:00:00+00:00", "equity": 3.0},
            {"ts": "2026-04-04T00:00:00+00:00", "equity": 4.0},
            {"ts": "2026-04-05T00:00:00+00:00", "equity": 5.0},
        ]

    monkeypatch.setattr(
        broker_proxy_module.BrokerProxy, "get_portfolio_history", fake_get
    )

    r = client.get("/api/portfolio/equity")
    assert r.status_code == 200
    body = r.json()
    # Journal-derived curve has 1 or 2 distinct days (depending on month edge)
    # — the key invariant is that the alpaca-flavoured equities (1..5) never
    # leak through when the journal has data.
    assert len(body["points"]) <= 2
    for p in body["points"]:
        assert p["equity"] not in {1.0, 2.0, 3.0, 4.0, 5.0}
    assert alpaca_called is False, "Alpaca fallback must not be called when journal has fills"


def test_portfolio_history_falls_back_to_empty_when_both_sources_fail(
    client: TestClient, monkeypatch
) -> None:
    """Empty journal + Alpaca returns None → endpoint cleanly returns empty points."""
    from dashboard.api import broker_proxy as broker_proxy_module

    def fake_get(self: Any, *, days: int = 90) -> list[dict] | None:
        return None

    monkeypatch.setattr(
        broker_proxy_module.BrokerProxy, "get_portfolio_history", fake_get
    )

    r = client.get("/api/portfolio/equity")
    assert r.status_code == 200
    body = r.json()
    assert body["points"] == []
    assert body["start_equity"] is None
    assert body["end_equity"] is None


def test_portfolio_history_does_not_call_alpaca_for_per_agent_view(
    client: TestClient, monkeypatch
) -> None:
    """agent= filter + empty journal → Alpaca fallback NOT invoked (it's joined-view-only)."""
    from dashboard.api import broker_proxy as broker_proxy_module

    called = False

    def fake_get(self: Any, *, days: int = 90) -> list[dict]:
        nonlocal called
        called = True
        return _stub_history_points()

    monkeypatch.setattr(
        broker_proxy_module.BrokerProxy, "get_portfolio_history", fake_get
    )

    r = client.get(
        "/api/portfolio/equity", params={"agent": "equity_agent", "days": 30}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["points"] == []
    assert body["agent"] == "equity_agent"
    assert called is False, "Alpaca fallback must not be used for per-agent views"


# ---------------------------------------------------------------------------
# BrokerProxy.get_portfolio_history unit behaviour.
# ---------------------------------------------------------------------------


class _FakeHistory:
    """Mimics alpaca-py's PortfolioHistory return shape."""

    def __init__(self, *, timestamp: list, equity: list) -> None:
        self.timestamp = timestamp
        self.equity = equity


def test_broker_proxy_get_portfolio_history_handles_disconnected_broker(
    monkeypatch,
) -> None:
    """Underlying client raises → method returns None, never propagates."""
    from dashboard.api import broker_proxy as broker_proxy_module

    proxy = broker_proxy_module.BrokerProxy()

    class _BoomClient:
        def get_portfolio_history(self, *args, **kwargs):
            raise ConnectionError("alpaca down")

    boom_client = _BoomClient()
    monkeypatch.setattr(proxy, "_get_client", lambda: boom_client)
    assert proxy.get_portfolio_history(days=90) is None


def test_broker_proxy_period_mapping() -> None:
    """days → period bucket mapping covers every documented threshold."""
    from dashboard.api.broker_proxy import BrokerProxy

    assert BrokerProxy._period_for_days(1) == "1D"
    assert BrokerProxy._period_for_days(5) == "1W"
    assert BrokerProxy._period_for_days(7) == "1W"
    assert BrokerProxy._period_for_days(30) == "1M"
    assert BrokerProxy._period_for_days(90) == "3M"
    assert BrokerProxy._period_for_days(200) == "1A"
    assert BrokerProxy._period_for_days(365) == "1A"
    assert BrokerProxy._period_for_days(2000) == "all"


def test_broker_proxy_skips_invalid_equity_values(monkeypatch) -> None:
    """None, NaN, and other non-finite equity entries are dropped silently."""
    from dashboard.api import broker_proxy as broker_proxy_module

    proxy = broker_proxy_module.BrokerProxy()

    # 4 timestamps, one each: None, 100000, NaN, 100100.
    base = int(datetime(2026, 4, 1, tzinfo=UTC).timestamp())
    history = _FakeHistory(
        timestamp=[base, base + 86_400, base + 2 * 86_400, base + 3 * 86_400],
        equity=[None, 100000.0, float("nan"), 100100.0],
    )

    class _Client:
        def get_portfolio_history(self, *args, **kwargs):
            return history

    fake_client = _Client()
    monkeypatch.setattr(proxy, "_get_client", lambda: fake_client)

    out = proxy.get_portfolio_history(days=90)
    assert out is not None
    # Only 100000 and 100100 remain.
    assert len(out) == 2
    equities = [p["equity"] for p in out]
    assert 100000.0 in equities
    assert 100100.0 in equities
    for p in out:
        assert math.isfinite(p["equity"])
        # ts is a parseable ISO string in UTC.
        datetime.fromisoformat(p["ts"])
