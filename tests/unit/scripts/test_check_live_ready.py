"""Tests for scripts/check_live_ready.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "check_live_ready.py"


@pytest.fixture
def clr_module():
    """Import scripts/check_live_ready.py as a module."""
    spec = importlib.util.spec_from_file_location("check_live_ready", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass machinery (under PEP 563 `from __future__
    # import annotations`) can resolve cls.__module__ from sys.modules.
    sys.modules["check_live_ready"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "backtests").mkdir()
    (tmp_path / "journal").mkdir()
    return tmp_path


def _write_metrics(repo: Path, strategy: str, run_id: str, payload: dict) -> Path:
    run_dir = repo / "backtests" / strategy / run_id
    run_dir.mkdir(parents=True)
    p = run_dir / "metrics.json"
    p.write_text(json.dumps(payload))
    return p


# ---------------------------------------------------------------------------
# audit_strategy: basic shape and defensiveness
# ---------------------------------------------------------------------------


def test_audit_strategy_returns_exactly_9_gates(clr_module, tmp_path):
    repo = _make_repo(tmp_path)
    result = clr_module.audit_strategy("nonexistent", date(2026, 5, 1), repo=repo)
    assert len(result.gates) == 9
    ids = [g.gate_id for g in result.gates]
    assert ids == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_audit_strategy_no_data_all_fail_no_crash(clr_module, tmp_path):
    repo = _make_repo(tmp_path)
    result = clr_module.audit_strategy("ghost", date(2026, 5, 1), repo=repo)
    assert result.all_passed is False
    # With no backtest output and no journal data, every gate fails with a
    # sensible reason and no crash.
    for g in result.gates:
        assert isinstance(g.reason, str) and len(g.reason) > 0
        assert g.passed is False, f"gate {g.gate_id} unexpectedly passed: {g}"


def test_audit_strategy_target_and_asof_recorded(clr_module, tmp_path):
    repo = _make_repo(tmp_path)
    result = clr_module.audit_strategy("foo", date(2026, 5, 1), repo=repo)
    assert result.target == "foo"
    assert result.asof == date(2026, 5, 1)


# ---------------------------------------------------------------------------
# Defensive: corrupt files do not crash
# ---------------------------------------------------------------------------


def test_corrupt_metrics_json_does_not_crash(clr_module, tmp_path):
    repo = _make_repo(tmp_path)
    run_dir = repo / "backtests" / "broken" / "20260101T000000Z"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text("this is not json {{{")
    result = clr_module.audit_strategy("broken", date(2026, 5, 1), repo=repo)
    # Gates that depend on backtest metrics should fail with a sensible reason.
    sharpe_gate = next(g for g in result.gates if g.gate_id == 2)
    assert sharpe_gate.passed is False
    assert "backtest" in sharpe_gate.reason.lower()


def test_corrupt_journal_line_does_not_crash(clr_module, tmp_path):
    repo = _make_repo(tmp_path)
    asof = date(2026, 5, 1)
    j = repo / "journal" / f"{asof.isoformat()}.jsonl"
    record = {
        "gate": "risk",
        "decision": "APPROVE",
        "ts": "2026-05-01T00:00:00+00:00",
        "strategy": "x",
    }
    j.write_text("this is not json\n" + json.dumps(record) + "\n")
    # Should not raise.
    result = clr_module.audit_strategy("x", asof, repo=repo)
    assert len(result.gates) == 9


# ---------------------------------------------------------------------------
# ReadinessResult.to_dict
# ---------------------------------------------------------------------------


def test_readiness_result_to_dict_serializes(clr_module, tmp_path):
    repo = _make_repo(tmp_path)
    result = clr_module.audit_strategy("any", date(2026, 5, 1), repo=repo)
    d = result.to_dict()
    assert d["target"] == "any"
    assert d["asof"] == "2026-05-01"
    assert isinstance(d["all_passed"], bool)
    assert isinstance(d["gates"], list)
    assert len(d["gates"]) == 9
    # Must round-trip through json.dumps without error.
    s = json.dumps(d)
    assert "target" in s


def test_gate_audit_to_dict_keys(clr_module, tmp_path):
    repo = _make_repo(tmp_path)
    result = clr_module.audit_strategy("any", date(2026, 5, 1), repo=repo)
    g = result.gates[0]
    d = g.to_dict()
    assert set(d.keys()) == {
        "gate_id",
        "name",
        "threshold",
        "actual",
        "passed",
        "reason",
    }


# ---------------------------------------------------------------------------
# CLI: --json prints valid JSON, exit code is 1 when failing
# ---------------------------------------------------------------------------


def test_cli_json_output_is_valid_json(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--strategy", "ghost", "--json", "--asof", "2026-05-01"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    # Exit code is 1 because no real data.
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["target"] == "ghost"
    assert payload["asof"] == "2026-05-01"
    assert isinstance(payload["gates"], list)
    assert len(payload["gates"]) == 9


def test_cli_exits_1_when_failing(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--strategy", "definitely-not-a-real-strategy"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1


def test_cli_exits_0_when_all_pass(tmp_path, monkeypatch):
    """Build a fixture repo where every gate can pass, run the CLI, expect 0."""
    repo = _make_repo(tmp_path)
    strategy = "alpha"
    asof = date(2026, 5, 1)
    # Backtest metrics
    _write_metrics(
        repo,
        strategy,
        "20260101T000000Z",
        {
            "sharpe": 1.0,
            "max_dd": 0.10,
            "win_rate": 0.5,
        },
    )
    # Journal: write enough records over a 200-day span to satisfy duration + count.
    journal_dir = repo / "journal"
    journal_dir.mkdir(exist_ok=True)
    # Day 1: a record to anchor span >= 180 days
    early = date.fromordinal(asof.toordinal() - 200)
    early_path = journal_dir / f"{early.isoformat()}.jsonl"
    early_path.write_text(
        json.dumps(
            {
                "event": "submit",
                "strategy": strategy,
                "ts": f"{early.isoformat()}T00:00:00+00:00",
            }
        )
        + "\n"
    )
    # Recent day: 150 fills + slippage + live metrics + pairwise corr
    recent = asof
    recent_path = journal_dir / f"{recent.isoformat()}.jsonl"
    lines = []
    for i in range(150):
        lines.append(
            json.dumps(
                {
                    "event": "fill",
                    "strategy": strategy,
                    "slippage_bps": 1.0,
                    "pnl": 5.0,  # all wins -> live_WR = 1.0, coherence = 2.0
                    "ts": f"{recent.isoformat()}T00:0{i % 10}:0{i % 10}+00:00",
                }
            )
        )
    # Live aggregates that beat the thresholds.
    lines.append(
        json.dumps(
            {
                "event": "live_summary",
                "strategy": strategy,
                "live_sharpe": 0.8,  # >= 0.7 * 1.0
                "live_max_dd": 0.05,  # <= 1.3 * 0.10
                "ts": f"{recent.isoformat()}T01:00:00+00:00",
            }
        )
    )
    # Pairwise correlation under 0.7
    lines.append(
        json.dumps(
            {
                "event": "pairwise_corr",
                "strategy": strategy,
                "other": "beta",
                "corr": 0.2,
                "ts": f"{recent.isoformat()}T02:00:00+00:00",
            }
        )
    )
    recent_path.write_text("\n".join(lines) + "\n")

    # Patch REPO via cwd so CLI uses our tmp repo. The script computes REPO from
    # its own __file__ path, so we must actually copy the script into tmp_path.
    fake_script = tmp_path / "scripts" / "check_live_ready.py"
    fake_script.parent.mkdir()
    fake_script.write_text(SCRIPT.read_text())

    proc = subprocess.run(
        [
            sys.executable,
            str(fake_script),
            "--strategy",
            strategy,
            "--asof",
            asof.isoformat(),
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"expected all gates to pass but got exit={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["all_passed"] is True
    failing = [g for g in payload["gates"] if not g["passed"]]
    assert failing == [], failing


# ---------------------------------------------------------------------------
# Portfolio audit
# ---------------------------------------------------------------------------


def test_audit_portfolio_returns_some_result(clr_module, tmp_path):
    repo = _make_repo(tmp_path)
    # Two empty strategy dirs.
    (repo / "backtests" / "alpha").mkdir()
    (repo / "backtests" / "beta").mkdir()
    result = clr_module.audit_portfolio(date(2026, 5, 1), repo=repo)
    assert result.target == "portfolio"
    # 9 gates per strategy times 2 strategies (since each has metrics absent
    # but gates still run) = 18.
    assert len(result.gates) == 18
    assert result.all_passed is False


def test_audit_portfolio_no_strategies(clr_module, tmp_path):
    repo = _make_repo(tmp_path)
    result = clr_module.audit_portfolio(date(2026, 5, 1), repo=repo)
    assert result.target == "portfolio"
    assert result.all_passed is False
    assert len(result.gates) >= 1
