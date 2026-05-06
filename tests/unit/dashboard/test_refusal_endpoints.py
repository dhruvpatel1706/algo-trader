"""Dashboard /api/refusals/recent endpoint tests.

Pinned properties:
  - Empty journal → returns ``[]`` (NEVER 500). The dashboard renders empty
    states; a missing journal file is normal on a fresh install.
  - Reason filter narrows the result set.
  - ``since`` filter narrows the result set by timestamp.
  - Limit clamps results.
  - Malformed rows are skipped, not raised.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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


def _write_journal_line(journal_dir: Path, day: str, payload: dict) -> None:
    path = journal_dir / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload))
        f.write("\n")


def _today_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Empty / missing journal
# ---------------------------------------------------------------------------


def test_recent_refusals_empty_journal_returns_empty_list(
    client: TestClient,
) -> None:
    r = client.get("/api/refusals/recent")
    assert r.status_code == 200
    assert r.json() == []


def test_recent_refusals_missing_journal_file_returns_empty_list(
    client: TestClient, tmp_path: Path
) -> None:
    """Today's file simply doesn't exist — should not 500."""
    # The fixture already creates an empty journal dir; nothing to do.
    r = client.get("/api/refusals/recent")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_recent_refusals_returns_journaled_events(
    client: TestClient, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    _write_journal_line(
        journal_dir,
        today,
        {
            "event": "refusal",
            "ts": _now_iso(),
            "reason": "reasoner_halt",
            "symbol": "SPY",
            "side": "buy",
            "strategy": "failed_breakout",
            "agent": "equity_agent",
            "signal_id": "sig-1",
            "detail": "regime mismatch",
            "extra": {"multiplier": 1.0},
        },
    )
    r = client.get("/api/refusals/recent")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    rec = body[0]
    assert rec["reason"] == "reasoner_halt"
    assert rec["symbol"] == "SPY"
    assert rec["strategy"] == "failed_breakout"
    assert rec["agent"] == "equity_agent"
    assert rec["detail"] == "regime mismatch"
    assert rec["extra"] == {"multiplier": 1.0}


def test_recent_refusals_ignores_non_refusal_events(
    client: TestClient, tmp_path: Path
) -> None:
    """Only event=refusal rows show up — fills/orders/etc. are skipped."""
    journal_dir = tmp_path / "journal"
    today = _today_str()
    _write_journal_line(
        journal_dir,
        today,
        {"event": "fill", "ts": _now_iso(), "subject": "SPY"},
    )
    _write_journal_line(
        journal_dir,
        today,
        {
            "event": "refusal",
            "ts": _now_iso(),
            "reason": "manual_stop",
            "detail": "operator hit kill",
        },
    )
    _write_journal_line(
        journal_dir,
        today,
        {"event": "submit", "ts": _now_iso(), "subject": "QQQ"},
    )

    r = client.get("/api/refusals/recent")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["reason"] == "manual_stop"


# ---------------------------------------------------------------------------
# reason filter
# ---------------------------------------------------------------------------


def test_recent_refusals_filter_by_reason(
    client: TestClient, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    for reason in ("reasoner_halt", "risk_cap_position", "reasoner_halt"):
        _write_journal_line(
            journal_dir,
            today,
            {
                "event": "refusal",
                "ts": _now_iso(),
                "reason": reason,
                "detail": f"detail for {reason}",
            },
        )

    r = client.get("/api/refusals/recent", params={"reason": "reasoner_halt"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert all(rec["reason"] == "reasoner_halt" for rec in body)


def test_recent_refusals_unknown_reason_returns_empty(
    client: TestClient, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    _write_journal_line(
        journal_dir,
        today,
        {
            "event": "refusal",
            "ts": _now_iso(),
            "reason": "manual_stop",
            "detail": "x",
        },
    )
    r = client.get("/api/refusals/recent", params={"reason": "nope_not_real"})
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# since filter
# ---------------------------------------------------------------------------


def test_recent_refusals_filter_by_since(
    client: TestClient, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    now = datetime.now(UTC)
    old_ts = (now - timedelta(hours=5)).isoformat()
    new_ts = (now - timedelta(minutes=5)).isoformat()
    _write_journal_line(
        journal_dir,
        today,
        {
            "event": "refusal",
            "ts": old_ts,
            "reason": "reasoner_halt",
            "detail": "old",
        },
    )
    _write_journal_line(
        journal_dir,
        today,
        {
            "event": "refusal",
            "ts": new_ts,
            "reason": "reasoner_halt",
            "detail": "new",
        },
    )

    cutoff = (now - timedelta(hours=1)).isoformat()
    r = client.get("/api/refusals/recent", params={"since": cutoff})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["detail"] == "new"


def test_recent_refusals_invalid_since_is_tolerated(
    client: TestClient, tmp_path: Path
) -> None:
    """Bad `since` should be ignored, not 400."""
    journal_dir = tmp_path / "journal"
    today = _today_str()
    _write_journal_line(
        journal_dir,
        today,
        {
            "event": "refusal",
            "ts": _now_iso(),
            "reason": "manual_stop",
            "detail": "x",
        },
    )
    r = client.get("/api/refusals/recent", params={"since": "not-a-timestamp"})
    assert r.status_code == 200
    body = r.json()
    # Bad input → no filter → return today's row.
    assert len(body) == 1


# ---------------------------------------------------------------------------
# limit
# ---------------------------------------------------------------------------


def test_recent_refusals_respects_limit(
    client: TestClient, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    for i in range(10):
        _write_journal_line(
            journal_dir,
            today,
            {
                "event": "refusal",
                "ts": (datetime.now(UTC) - timedelta(seconds=i)).isoformat(),
                "reason": "reasoner_halt",
                "detail": f"row {i}",
            },
        )
    r = client.get("/api/refusals/recent", params={"limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3


def test_recent_refusals_limit_validation(client: TestClient) -> None:
    """limit > 1000 → 422 (FastAPI validation)."""
    r = client.get("/api/refusals/recent", params={"limit": 5000})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Malformed row tolerance
# ---------------------------------------------------------------------------


def test_recent_refusals_skips_malformed_rows(
    client: TestClient, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    # Missing required `reason`.
    _write_journal_line(
        journal_dir,
        today,
        {"event": "refusal", "ts": _now_iso(), "detail": "no reason"},
    )
    # Missing `ts`.
    _write_journal_line(
        journal_dir,
        today,
        {"event": "refusal", "reason": "manual_stop", "detail": "no ts"},
    )
    # Valid one.
    _write_journal_line(
        journal_dir,
        today,
        {
            "event": "refusal",
            "ts": _now_iso(),
            "reason": "manual_stop",
            "detail": "valid",
        },
    )
    r = client.get("/api/refusals/recent")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["detail"] == "valid"


def test_recent_refusals_newest_first_ordering(
    client: TestClient, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "journal"
    today = _today_str()
    now = datetime.now(UTC)
    _write_journal_line(
        journal_dir,
        today,
        {
            "event": "refusal",
            "ts": (now - timedelta(hours=2)).isoformat(),
            "reason": "manual_stop",
            "detail": "older",
        },
    )
    _write_journal_line(
        journal_dir,
        today,
        {
            "event": "refusal",
            "ts": (now - timedelta(minutes=2)).isoformat(),
            "reason": "manual_stop",
            "detail": "newer",
        },
    )
    r = client.get("/api/refusals/recent")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert body[0]["detail"] == "newer"
    assert body[1]["detail"] == "older"
