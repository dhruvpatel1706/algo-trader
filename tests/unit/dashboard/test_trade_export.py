"""Tests for ``GET /api/trades/export.csv``.

Pinned properties:
  - Empty journal → header-only CSV, status 200 (NEVER 500). The user
    expects "give me my trades" to always download something.
  - Column order matches :data:`dashboard.api.trade_export.CSV_COLUMNS`.
  - Open trades (no close event) emit empty exit fields.
  - ``strategy`` filter narrows the result set.
  - ``from``/``to`` date filters narrow the result set.
  - Malformed rows are skipped, valid ones pass through.
  - ``Content-Type``/``Content-Disposition`` headers are correctly set.
  - Body parses cleanly with :class:`csv.DictReader`.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dashboard.api.trade_export import CSV_COLUMNS
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    """TestClient with the journal directory isolated to ``tmp_path``."""
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

    from dashboard.api.main import app

    return TestClient(app)


def _write(journal_dir: Path, day: str, payload: dict) -> None:
    path = journal_dir / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload))
        f.write("\n")


def _today_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_csv(body: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(body))
    rows = list(reader)
    return list(reader.fieldnames or []), rows


# ---------------------------------------------------------------------------
# Empty / missing journal
# ---------------------------------------------------------------------------


def test_export_empty_journal_returns_header_only(client: TestClient) -> None:
    r = client.get("/api/trades/export.csv")
    assert r.status_code == 200
    fieldnames, rows = _parse_csv(r.text)
    assert tuple(fieldnames) == CSV_COLUMNS
    assert rows == []


def test_export_column_order_pinned(client: TestClient) -> None:
    """The CSV column order is part of the contract — pin it."""
    r = client.get("/api/trades/export.csv")
    assert r.status_code == 200
    # Just inspect the header line directly to be unambiguous.
    header_line = r.text.splitlines()[0]
    assert header_line == ",".join(CSV_COLUMNS)


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def test_export_sets_csv_content_type(client: TestClient) -> None:
    r = client.get("/api/trades/export.csv")
    assert r.status_code == 200
    # FastAPI lowercases header names, but the value should declare CSV.
    ct = r.headers["content-type"]
    assert ct.startswith("text/csv")
    assert "charset=utf-8" in ct


def test_export_sets_attachment_disposition(client: TestClient) -> None:
    r = client.get("/api/trades/export.csv")
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment; ")
    assert "filename=\"trades_" in cd
    assert cd.endswith('.csv"')


def test_export_sets_no_cache(client: TestClient) -> None:
    r = client.get("/api/trades/export.csv")
    assert r.headers["cache-control"] == "no-cache"


# ---------------------------------------------------------------------------
# Happy path — paired entry + close
# ---------------------------------------------------------------------------


def test_export_paired_entry_and_close(client: TestClient, tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    coid = "client-coid-1"
    _write(
        journal_dir,
        today,
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": coid,
            "broker_order_id": "broker-1",
            "symbol": "SPY",
            "side": "buy",
            "qty": 10,
            "fill_price": 500.0,
            "strategy": "mr_etf",
            "agent": "equity_agent",
            "cycle_id": "cycle-1",
            "status": "filled",
        },
    )
    _write(
        journal_dir,
        today,
        {
            "event": "trade_closed",
            "ts": _now_iso(),
            "client_order_id": coid,
            "broker_order_id": "broker-1",
            "symbol": "SPY",
            "exit_price": 510.0,
            "pnl_usd": 100.0,
            "pnl_pct": 0.02,
            "pnl_r": 1.5,
            "status": "filled",
        },
    )
    r = client.get("/api/trades/export.csv")
    assert r.status_code == 200
    fieldnames, rows = _parse_csv(r.text)
    assert tuple(fieldnames) == CSV_COLUMNS
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "SPY"
    assert row["side"] == "buy"
    assert row["qty"] == "10"
    assert row["entry_price"] == "500"
    assert row["exit_price"] == "510"
    assert row["pnl_usd"] == "100"
    assert row["pnl_pct"] == "0.02"
    assert row["pnl_r"] == "1.5"
    assert row["strategy"] == "mr_etf"
    assert row["agent"] == "equity_agent"
    assert row["broker_order_id"] == "broker-1"
    assert row["client_order_id"] == coid
    assert row["status"] == "filled"
    assert row["cycle_id"] == "cycle-1"


def test_export_open_trade_has_empty_exit_fields(
    client: TestClient, tmp_path: Path
) -> None:
    """A trade with no close event still appears, with empty exit columns."""
    journal_dir = tmp_path / "journal"
    today = _today_str()
    _write(
        journal_dir,
        today,
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": "open-1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 5,
            "fill_price": 200.0,
            "strategy": "mr_etf",
            "status": "filled",
        },
    )
    r = client.get("/api/trades/export.csv")
    _, rows = _parse_csv(r.text)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "AAPL"
    assert row["entry_price"] == "200"
    assert row["exit_price"] == ""
    assert row["pnl_usd"] == ""
    assert row["pnl_pct"] == ""
    assert row["pnl_r"] == ""


def test_export_submit_dry_run_only_empty_entry_price(
    client: TestClient, tmp_path: Path
) -> None:
    """Dry-run submit without a fill price → empty entry/exit prices but row exists."""
    journal_dir = tmp_path / "journal"
    today = _today_str()
    _write(
        journal_dir,
        today,
        {
            "event": "submit_dry_run",
            "ts": _now_iso(),
            "client_order_id": "dryrun-1",
            "symbol": "TSLA",
            "side": "buy",
            "qty": 3,
        },
    )
    r = client.get("/api/trades/export.csv")
    _, rows = _parse_csv(r.text)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "TSLA"
    assert rows[0]["entry_price"] == ""
    assert rows[0]["exit_price"] == ""


# ---------------------------------------------------------------------------
# Strategy filter
# ---------------------------------------------------------------------------


def test_export_strategy_filter_narrows_results(
    client: TestClient, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    _write(
        journal_dir,
        today,
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": "a",
            "symbol": "SPY",
            "side": "buy",
            "qty": 1,
            "fill_price": 1.0,
            "strategy": "mr_etf",
        },
    )
    _write(
        journal_dir,
        today,
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": "b",
            "symbol": "QQQ",
            "side": "buy",
            "qty": 1,
            "fill_price": 1.0,
            "strategy": "failed_breakout",
        },
    )
    r = client.get("/api/trades/export.csv", params={"strategy": "mr_etf"})
    _, rows = _parse_csv(r.text)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["strategy"] == "mr_etf"


def test_export_strategy_filter_empty_match_returns_header_only(
    client: TestClient, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    _write(
        journal_dir,
        today,
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": "a",
            "symbol": "SPY",
            "side": "buy",
            "qty": 1,
            "fill_price": 1.0,
            "strategy": "mr_etf",
        },
    )
    r = client.get("/api/trades/export.csv", params={"strategy": "nope_unknown"})
    _, rows = _parse_csv(r.text)
    assert rows == []


# ---------------------------------------------------------------------------
# from/to date filter
# ---------------------------------------------------------------------------


def test_export_date_range_filter(client: TestClient, tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    _write(
        journal_dir,
        yesterday.strftime("%Y-%m-%d"),
        {
            "event": "fill",
            "ts": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "client_order_id": "yesterday-coid",
            "symbol": "OLD",
            "side": "buy",
            "qty": 1,
            "fill_price": 1.0,
            "strategy": "mr_etf",
        },
    )
    _write(
        journal_dir,
        today.strftime("%Y-%m-%d"),
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": "today-coid",
            "symbol": "NEW",
            "side": "buy",
            "qty": 1,
            "fill_price": 1.0,
            "strategy": "mr_etf",
        },
    )
    # Restrict to today only.
    r = client.get(
        "/api/trades/export.csv",
        params={"from": today.isoformat(), "to": today.isoformat()},
    )
    _, rows = _parse_csv(r.text)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NEW"


def test_export_date_range_filename_reflects_window(
    client: TestClient, tmp_path: Path
) -> None:
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    r = client.get(
        "/api/trades/export.csv",
        params={"from": yesterday.isoformat(), "to": today.isoformat()},
    )
    cd = r.headers["content-disposition"]
    assert yesterday.isoformat() in cd
    assert today.isoformat() in cd


# ---------------------------------------------------------------------------
# Malformed rows
# ---------------------------------------------------------------------------


def test_export_skips_malformed_rows(client: TestClient, tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    # Trade event without client_order_id — cannot pair, must skip.
    _write(
        journal_dir,
        today,
        {"event": "fill", "ts": _now_iso(), "symbol": "FOO", "side": "buy", "qty": 1},
    )
    # Trade event with missing symbol — no useful row.
    _write(
        journal_dir,
        today,
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": "no-symbol",
            "side": "buy",
            "qty": 1,
        },
    )
    # Valid event.
    _write(
        journal_dir,
        today,
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": "good-1",
            "symbol": "BAR",
            "side": "sell",
            "qty": 2,
            "fill_price": 99.5,
            "strategy": "mr_etf",
        },
    )
    r = client.get("/api/trades/export.csv")
    assert r.status_code == 200
    _, rows = _parse_csv(r.text)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BAR"
    assert rows[0]["side"] == "sell"


def test_export_handles_corrupt_jsonl_lines(
    client: TestClient, tmp_path: Path
) -> None:
    """Garbage lines mid-file must not blow up the export."""
    journal_dir = tmp_path / "journal"
    today = _today_str()
    path = journal_dir / f"{today}.jsonl"
    valid = json.dumps(
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": "g1",
            "symbol": "SPY",
            "side": "buy",
            "qty": 1,
            "fill_price": 1.0,
        }
    )
    path.write_text(f"not-json\n{valid}\n{{partial\n", encoding="utf-8")
    r = client.get("/api/trades/export.csv")
    assert r.status_code == 200
    _, rows = _parse_csv(r.text)
    assert len(rows) == 1
    assert rows[0]["client_order_id"] == "g1"


# ---------------------------------------------------------------------------
# CSV body validity
# ---------------------------------------------------------------------------


def test_export_body_is_valid_csv(client: TestClient, tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    for i in range(3):
        _write(
            journal_dir,
            today,
            {
                "event": "fill",
                "ts": _now_iso(),
                "client_order_id": f"coid-{i}",
                "symbol": "SPY",
                "side": "buy",
                "qty": i + 1,
                "fill_price": 100.0 + i,
                "strategy": "mr_etf",
            },
        )
    r = client.get("/api/trades/export.csv")
    fieldnames, rows = _parse_csv(r.text)
    assert tuple(fieldnames) == CSV_COLUMNS
    assert len(rows) == 3
    # Every row has every column key — DictReader will fill blanks if not.
    for row in rows:
        assert set(row.keys()) == set(CSV_COLUMNS)


def test_export_pairing_uses_fill_price_over_submit(
    client: TestClient, tmp_path: Path
) -> None:
    """A submit followed by a fill should record the fill's entry price."""
    journal_dir = tmp_path / "journal"
    today = _today_str()
    coid = "coid-1"
    _write(
        journal_dir,
        today,
        {
            "event": "submit",
            "ts": _now_iso(),
            "client_order_id": coid,
            "symbol": "SPY",
            "side": "buy",
            "qty": 1,
            "limit_price": 500.0,
            "strategy": "mr_etf",
        },
    )
    _write(
        journal_dir,
        today,
        {
            "event": "fill",
            "ts": _now_iso(),
            "client_order_id": coid,
            "symbol": "SPY",
            "side": "buy",
            "qty": 1,
            "fill_price": 501.25,
            "strategy": "mr_etf",
        },
    )
    r = client.get("/api/trades/export.csv")
    _, rows = _parse_csv(r.text)
    assert len(rows) == 1
    assert rows[0]["entry_price"] == "501.25"
