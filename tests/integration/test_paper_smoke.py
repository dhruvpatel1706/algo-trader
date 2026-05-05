"""Integration smoke: full gate chain -> scripts/place_order.py --dry-run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent


def _journal_path_today(journal_dir: Path) -> Path:
    return journal_dir / f"{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"


def _write_approvals(
    journal_dir: Path,
    cycle_id: str,
    *,
    age: timedelta | None = None,
) -> None:
    path = _journal_path_today(journal_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = (datetime.now(UTC) - age).isoformat() if age else datetime.now(UTC).isoformat()
    path.write_text(
        json.dumps(
            {
                "ts": ts,
                "gate": "risk",
                "decision": "APPROVE",
                "cycle_id": cycle_id,
                "size": 1,
                "reason": "smoke: qty=1",
            }
        )
        + "\n"
        + json.dumps(
            {
                "ts": ts,
                "gate": "compliance",
                "decision": "APPROVE",
                "cycle_id": cycle_id,
                "reason": "smoke: all pass",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _make_fake_repo(tmp_path: Path) -> Path:
    fake_repo = tmp_path / "algo-trader"
    fake_repo.mkdir()
    (fake_repo / "journal").mkdir()
    (fake_repo / "scripts").symlink_to(REPO / "scripts")
    (fake_repo / "src").symlink_to(REPO / "src")
    return fake_repo


def _env(fake_repo: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["ALPACA_PAPER_TRADE"] = "True"
    env["ALPACA_API_KEY"] = "test_key"
    env["ALPACA_SECRET_KEY"] = "test_secret"
    env["LIVE_TRADING"] = "0"
    env["PYTHONPATH"] = str(fake_repo)
    return env


def _run_place_order(fake_repo: Path, *extra: str, cycle_id: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(fake_repo / "scripts" / "place_order.py"),
            "--paper",
            "--symbol",
            "SPY",
            "--qty",
            "1",
            "--side",
            "buy",
            "--type",
            "market",
            "--cycle-id",
            cycle_id,
            "--repo-root",
            str(fake_repo),
            *extra,
        ],
        capture_output=True,
        text=True,
        env=_env(fake_repo),
        cwd=fake_repo,
    )


@pytest.mark.integration
def test_place_order_dry_run_round_trips_through_both_gates(tmp_path):
    """Simulate the orchestrator cycle: gates APPROVE -> place_order --dry-run -> journal record."""
    fake_repo = _make_fake_repo(tmp_path)
    cycle_id = "ci-smoke-001"
    _write_approvals(fake_repo / "journal", cycle_id)

    result = _run_place_order(fake_repo, "--dry-run", cycle_id=cycle_id)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert '"ok": true' in result.stdout or '"ok":true' in result.stdout
    isolated_journal = _journal_path_today(fake_repo / "journal")
    assert isolated_journal.exists()
    contents = isolated_journal.read_text(encoding="utf-8")
    assert "submit_dry_run" in contents
    assert cycle_id in contents


@pytest.mark.integration
def test_place_order_rejects_stale_approvals(tmp_path):
    """Approvals older than the freshness window must be rejected.

    Why this matters under real capital: a morning approval cannot authorize
    an afternoon order, even if both were for the same symbol — every order
    needs a fresh, cycle-bound approval pair.
    """
    fake_repo = _make_fake_repo(tmp_path)
    cycle_id = "stale-cycle"
    # Write approvals 600s old, then run with default 300s freshness window.
    _write_approvals(fake_repo / "journal", cycle_id, age=timedelta(seconds=600))

    result = _run_place_order(fake_repo, "--dry-run", cycle_id=cycle_id)
    assert result.returncode == 2
    assert "no fresh risk + compliance APPROVE pair" in result.stderr


@pytest.mark.integration
def test_place_order_rejects_wrong_cycle_id(tmp_path):
    """An approval pair from cycle X cannot authorize an order from cycle Y."""
    fake_repo = _make_fake_repo(tmp_path)
    _write_approvals(fake_repo / "journal", "approved-cycle")

    result = _run_place_order(fake_repo, "--dry-run", cycle_id="different-cycle")
    assert result.returncode == 2
    assert "no fresh risk + compliance APPROVE pair" in result.stderr
