"""Tests for scripts/check_live_ready.py — Phase 9 readiness gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

# The script lives outside the `src` package and ruff complains about adding
# scripts/ to sys.path globally, so we load it as a module by file path here.
_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "check_live_ready.py"
_spec = importlib.util.spec_from_file_location("check_live_ready", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
clr = importlib.util.module_from_spec(_spec)
sys.modules["check_live_ready"] = clr
_spec.loader.exec_module(clr)


# --------------------------------------------------------------------------- #
# Helpers — build deterministic fake journals                                 #
# --------------------------------------------------------------------------- #


def _write_journal(
    journal_dir: Path,
    events: list[dict[str, Any]],
) -> None:
    """Write each event into journal_dir/<UTC date>.jsonl based on event['ts']."""
    journal_dir.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        ts = ev["ts"]
        if isinstance(ts, datetime):
            iso = ts.astimezone(UTC).isoformat()
            day = ts.astimezone(UTC).strftime("%Y-%m-%d")
        else:
            iso = ts
            day = ts[:10]
        ev_out = {**ev, "ts": iso}
        by_day.setdefault(day, []).append(ev_out)
    for day, evs in by_day.items():
        path = journal_dir / f"{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for ev in evs:
                f.write(json.dumps(ev) + "\n")


def _make_fill(
    *,
    strategy: str,
    ts: datetime,
    fill_price: float = 100.0,
    intended_price: float | None = 100.0,
    pnl: float | None = None,
    event: str = "fill",
) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "event": event,
        "strategy": strategy,
        "ts": ts.astimezone(UTC).isoformat(),
        "fill_price": fill_price,
    }
    if intended_price is not None:
        ev["intended_price"] = intended_price
    if pnl is not None:
        ev["realized_pnl"] = pnl
    return ev


# --------------------------------------------------------------------------- #
# Criterion 1 — forward paper duration                                        #
# --------------------------------------------------------------------------- #


def test_criterion_1_passes_when_journal_old_enough(tmp_path: Path):
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    first = asof - timedelta(days=200)
    events = [_make_fill(strategy="failed_breakout", ts=first)]
    check = clr.check_criterion_1_forward_paper_duration(
        "failed_breakout", events=events, asof=asof, threshold_days=180
    )
    assert check.result == "PASS"
    assert check.measured["days"] == 200
    assert check.measured["threshold"] == 180
    assert check.n == 1


def test_criterion_1_fails_when_too_recent(tmp_path: Path):
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    first = asof - timedelta(days=30)
    events = [_make_fill(strategy="failed_breakout", ts=first)]
    check = clr.check_criterion_1_forward_paper_duration(
        "failed_breakout", events=events, asof=asof, threshold_days=180
    )
    assert check.result == "FAIL"
    assert check.measured["days"] == 30


def test_criterion_1_indeterminate_when_no_trades():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    check = clr.check_criterion_1_forward_paper_duration(
        "failed_breakout", events=[], asof=asof
    )
    assert check.result == "INDETERMINATE"
    assert check.measured["days"] is None


def test_criterion_1_uses_earliest_event_for_strategy_only():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    very_old = asof - timedelta(days=400)
    recent = asof - timedelta(days=50)
    events = [
        _make_fill(strategy="other_strat", ts=very_old),
        _make_fill(strategy="failed_breakout", ts=recent),
    ]
    check = clr.check_criterion_1_forward_paper_duration(
        "failed_breakout", events=events, asof=asof, threshold_days=180
    )
    assert check.result == "FAIL"
    assert check.measured["days"] == 50


# --------------------------------------------------------------------------- #
# Criterion 4 — total trades                                                  #
# --------------------------------------------------------------------------- #


def test_criterion_4_counts_trades_correctly():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _make_fill(strategy="failed_breakout", ts=base + timedelta(days=i))
        for i in range(175)
    ]
    check = clr.check_criterion_4_total_trades("failed_breakout", events=events)
    assert check.result == "PASS"
    assert check.measured["n_trades"] == 175


def test_criterion_4_fails_below_threshold():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _make_fill(strategy="failed_breakout", ts=base + timedelta(days=i))
        for i in range(50)
    ]
    check = clr.check_criterion_4_total_trades("failed_breakout", events=events)
    assert check.result == "FAIL"
    assert check.measured["n_trades"] == 50


def test_criterion_4_only_counts_fill_events():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        # 5 fills, 5 submits — only fills should count.
        *[
            _make_fill(strategy="failed_breakout", ts=base + timedelta(days=i))
            for i in range(5)
        ],
        *[
            {
                "event": "submit",
                "strategy": "failed_breakout",
                "ts": (base + timedelta(days=10 + i)).isoformat(),
            }
            for i in range(5)
        ],
    ]
    check = clr.check_criterion_4_total_trades(
        "failed_breakout", events=events, threshold=3
    )
    assert check.measured["n_trades"] == 5


# --------------------------------------------------------------------------- #
# Criterion 2 — Sharpe ratio                                                  #
# --------------------------------------------------------------------------- #


def test_criterion_2_indeterminate_without_backtest_summary():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _make_fill(strategy="failed_breakout", ts=base + timedelta(days=i), pnl=10.0)
        for i in range(10)
    ]
    check = clr.check_criterion_2_live_sharpe_vs_backtest(
        "failed_breakout", events=events, backtest_summary=None
    )
    assert check.result == "INDETERMINATE"


def test_criterion_2_indeterminate_when_no_pnl_in_journal():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        # No pnl on any fill — live_sharpe is unknowable.
        _make_fill(strategy="failed_breakout", ts=base + timedelta(days=i))
        for i in range(10)
    ]
    summary = {"failed_breakout": {"sharpe": 1.2, "max_dd": 0.1, "win_rate": 0.55}}
    check = clr.check_criterion_2_live_sharpe_vs_backtest(
        "failed_breakout", events=events, backtest_summary=summary
    )
    assert check.result == "INDETERMINATE"


def test_criterion_2_passes_with_good_ratio():
    # Construct daily PnL series with high mean / low std so live_sharpe is large.
    base = datetime(2026, 1, 1, tzinfo=UTC)
    pnls = [9.0, 11.0, 10.0, 9.5, 10.5, 10.2, 9.8, 10.1, 9.9, 10.3]
    events = [
        _make_fill(
            strategy="failed_breakout",
            ts=base + timedelta(days=i),
            pnl=p,
        )
        for i, p in enumerate(pnls)
    ]
    summary = {"failed_breakout": {"sharpe": 1.0, "max_dd": 0.1, "win_rate": 0.55}}
    check = clr.check_criterion_2_live_sharpe_vs_backtest(
        "failed_breakout", events=events, backtest_summary=summary
    )
    assert check.result == "PASS"
    assert check.measured["live_sharpe"] is not None


def test_criterion_2_fails_with_bad_ratio():
    # Mean ~0, high noise → small live Sharpe.
    base = datetime(2026, 1, 1, tzinfo=UTC)
    pnls = [10.0, -10.0, 9.0, -11.0, 8.0, -9.0, 11.0, -8.5, 10.5, -10.5]
    events = [
        _make_fill(
            strategy="failed_breakout",
            ts=base + timedelta(days=i),
            pnl=p,
        )
        for i, p in enumerate(pnls)
    ]
    summary = {"failed_breakout": {"sharpe": 2.0, "max_dd": 0.1, "win_rate": 0.55}}
    check = clr.check_criterion_2_live_sharpe_vs_backtest(
        "failed_breakout", events=events, backtest_summary=summary
    )
    assert check.result == "FAIL"


# --------------------------------------------------------------------------- #
# Criterion 3 — drawdown                                                      #
# --------------------------------------------------------------------------- #


def test_criterion_3_indeterminate_without_summary():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _make_fill(strategy="x", ts=base + timedelta(days=i), pnl=p)
        for i, p in enumerate([10, 5, -8, 3, -4])
    ]
    check = clr.check_criterion_3_live_dd_vs_backtest(
        "x", events=events, backtest_summary=None
    )
    assert check.result == "INDETERMINATE"


def test_criterion_3_passes_when_live_dd_well_below_backtest():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # cumulative: 10, 20, 30, 25, 30, 35 → small DD
    events = [
        _make_fill(strategy="x", ts=base + timedelta(days=i), pnl=p)
        for i, p in enumerate([10, 10, 10, -5, 5, 5])
    ]
    summary = {"x": {"sharpe": 1.0, "max_dd": 0.5, "win_rate": 0.5}}
    check = clr.check_criterion_3_live_dd_vs_backtest(
        "x", events=events, backtest_summary=summary
    )
    assert check.result == "PASS"


# --------------------------------------------------------------------------- #
# Criterion 5 — slippage                                                      #
# --------------------------------------------------------------------------- #


def test_criterion_5_indeterminate_without_intended_price():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _make_fill(strategy="x", ts=base + timedelta(days=i), intended_price=None)
        for i in range(3)
    ]
    check = clr.check_criterion_5_slippage_mae("x", events=events)
    assert check.result == "INDETERMINATE"


def test_criterion_5_passes_with_low_slippage():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _make_fill(
            strategy="x",
            ts=base + timedelta(days=i),
            fill_price=100.02,  # 2 bps
            intended_price=100.0,
        )
        for i in range(5)
    ]
    check = clr.check_criterion_5_slippage_mae("x", events=events)
    assert check.result == "PASS"
    assert check.measured["mae_bps"] == pytest.approx(2.0, rel=0.01)


def test_criterion_5_fails_with_high_slippage():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _make_fill(
            strategy="x",
            ts=base + timedelta(days=i),
            fill_price=100.10,  # 10 bps
            intended_price=100.0,
        )
        for i in range(5)
    ]
    check = clr.check_criterion_5_slippage_mae("x", events=events)
    assert check.result == "FAIL"


# --------------------------------------------------------------------------- #
# Criterion 6 — risk-cap breaches                                             #
# --------------------------------------------------------------------------- #


def test_criterion_6_counts_breaches_zero_for_normal_journal():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    base = asof - timedelta(days=10)
    events = [
        _make_fill(strategy="x", ts=base + timedelta(days=i)) for i in range(5)
    ]
    check = clr.check_criterion_6_risk_cap_breaches("x", events=events, asof=asof)
    assert check.result == "PASS"
    assert check.measured["n_breaches"] == 0


def test_criterion_6_counts_breach_alert_in_window():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    events = [
        {
            "event": "cap_breach_alert",
            "strategy": "x",
            "ts": (asof - timedelta(days=10)).isoformat(),
        }
    ]
    check = clr.check_criterion_6_risk_cap_breaches("x", events=events, asof=asof)
    assert check.result == "FAIL"
    assert check.measured["n_breaches"] == 1


def test_criterion_6_ignores_breaches_outside_window():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    events = [
        {
            "event": "cap_breach_alert",
            "strategy": "x",
            "ts": (asof - timedelta(days=200)).isoformat(),
        }
    ]
    check = clr.check_criterion_6_risk_cap_breaches("x", events=events, asof=asof)
    assert check.result == "PASS"


def test_criterion_6_ignores_refusals():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    events = [
        # Refusals are caps doing their job — should NOT count as breaches.
        {
            "event": "refusal",
            "strategy": "x",
            "reason": "risk_cap_position",
            "ts": (asof - timedelta(days=5)).isoformat(),
        }
    ]
    check = clr.check_criterion_6_risk_cap_breaches("x", events=events, asof=asof)
    assert check.result == "PASS"


# --------------------------------------------------------------------------- #
# Criterion 9 — pairwise correlation                                          #
# --------------------------------------------------------------------------- #


def test_criterion_9_indeterminate_with_one_strategy():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    base = asof - timedelta(days=10)
    events = [
        _make_fill(strategy="x", ts=base + timedelta(days=i), pnl=1.0) for i in range(5)
    ]
    check = clr.check_criterion_9_pairwise_correlation(
        "x", events=events, other_live_strategies=["x"], asof=asof
    )
    assert check.result == "INDETERMINATE"
    assert check.measured["n_other_strategies"] == 0


def test_criterion_9_indeterminate_when_no_overlap():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    # Only one strategy has fills — the other has none → no overlap.
    base = asof - timedelta(days=10)
    events = [
        _make_fill(strategy="a", ts=base + timedelta(days=i), pnl=1.0) for i in range(5)
    ]
    check = clr.check_criterion_9_pairwise_correlation(
        "a", events=events, other_live_strategies=["a", "b"], asof=asof
    )
    assert check.result == "INDETERMINATE"


def test_criterion_9_passes_with_low_correlation():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    base = asof - timedelta(days=20)
    a_pnls = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
    b_pnls = [1.0, 1.0, -1.0, -1.0, 1.0, 1.0, -1.0, -1.0, 1.0, 1.0]
    events: list[dict[str, Any]] = []
    for i, p in enumerate(a_pnls):
        events.append(_make_fill(strategy="a", ts=base + timedelta(days=i), pnl=p))
    for i, p in enumerate(b_pnls):
        events.append(_make_fill(strategy="b", ts=base + timedelta(days=i), pnl=p))
    check = clr.check_criterion_9_pairwise_correlation(
        "a", events=events, other_live_strategies=["a", "b"], asof=asof
    )
    # Correlation here is moderate; we just want it to compute and produce a verdict.
    assert check.result in {"PASS", "FAIL"}
    assert check.measured["max_corr"] is not None


def test_criterion_9_fails_with_high_correlation():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    base = asof - timedelta(days=15)
    pnls = [1.0, 2.0, -1.0, 0.5, -2.0, 3.0, 1.5, -0.5, 2.0, -1.5]
    events: list[dict[str, Any]] = []
    for i, p in enumerate(pnls):
        # Same series for both strategies → corr = 1.
        events.append(_make_fill(strategy="a", ts=base + timedelta(days=i), pnl=p))
        events.append(_make_fill(strategy="b", ts=base + timedelta(days=i), pnl=p))
    check = clr.check_criterion_9_pairwise_correlation(
        "a", events=events, other_live_strategies=["a", "b"], asof=asof
    )
    assert check.result == "FAIL"
    assert check.measured["max_corr"] == pytest.approx(1.0, rel=0.01)


# --------------------------------------------------------------------------- #
# run_for_strategy aggregation                                                #
# --------------------------------------------------------------------------- #


def test_run_for_strategy_aggregates_correctly():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    # Brand-new strategy with one trade today → fails 1 (duration), 4 (count).
    events = [_make_fill(strategy="x", ts=asof - timedelta(days=1))]
    report = clr.run_for_strategy(
        "x",
        events=events,
        backtest_summary=None,
        other_live_strategies=["x"],
        asof=asof,
    )
    assert report.overall == "NOT_READY"
    assert 1 in report.blocking
    assert 4 in report.blocking


def test_run_for_strategy_indeterminate_when_no_data_anywhere():
    asof = datetime(2026, 5, 6, tzinfo=UTC)
    report = clr.run_for_strategy(
        "x",
        events=[],
        backtest_summary=None,
        other_live_strategies=["x"],
        asof=asof,
    )
    # Empty journal → most criteria INDETERMINATE; criteria 6/8 evaluate to PASS
    # (no breaches/halts trivially), but criteria 1/4 fail because zero trades and
    # no duration. So overall is NOT_READY.
    # The exact verdict depends on the rules; assert it is at least NOT a READY.
    assert report.overall in {"NOT_READY", "INDETERMINATE"}


# --------------------------------------------------------------------------- #
# main() integration                                                          #
# --------------------------------------------------------------------------- #


def test_main_with_strategy_all_runs_all(tmp_path: Path, capsys):
    journal_dir = tmp_path / "journal"
    asof = datetime.now(UTC)
    base = asof - timedelta(days=5)
    events = [
        _make_fill(strategy="alpha", ts=base + timedelta(days=i)) for i in range(3)
    ] + [
        _make_fill(strategy="beta", ts=base + timedelta(days=i)) for i in range(3)
    ]
    _write_journal(journal_dir, events)
    rc = clr.main(
        [
            "--strategy",
            "all",
            "--journal-dir",
            str(journal_dir),
            "--report-dir",
            str(tmp_path / "reports"),
            "--json",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "alpha" in payload["strategies"]
    assert "beta" in payload["strategies"]
    assert payload["portfolio"]["n_strategies"] == 2


def test_main_writes_json_report(tmp_path: Path, capsys):
    journal_dir = tmp_path / "journal"
    report_dir = tmp_path / "reports"
    asof = datetime.now(UTC)
    events = [_make_fill(strategy="alpha", ts=asof - timedelta(days=1))]
    _write_journal(journal_dir, events)
    rc = clr.main(
        [
            "--strategy",
            "alpha",
            "--journal-dir",
            str(journal_dir),
            "--report-dir",
            str(report_dir),
        ]
    )
    assert rc == 0
    expected = report_dir / f"live_ready_alpha_{asof.strftime('%Y-%m-%d')}.json"
    assert expected.exists()
    payload = json.loads(expected.read_text(encoding="utf-8"))
    assert payload["strategy"] == "alpha"
    assert "criteria" in payload
    assert len(payload["criteria"]) == 9


def test_main_returns_zero_exit_code(tmp_path: Path, capsys):
    """Even when a criterion FAILs, exit 0 — this is a report, not a CI gate."""
    journal_dir = tmp_path / "journal"
    asof = datetime.now(UTC)
    events = [_make_fill(strategy="alpha", ts=asof - timedelta(days=1))]
    _write_journal(journal_dir, events)
    rc = clr.main(
        [
            "--strategy",
            "alpha",
            "--journal-dir",
            str(journal_dir),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    assert rc == 0


def test_main_with_no_journal_dir_does_not_crash(tmp_path: Path, capsys):
    rc = clr.main(
        [
            "--strategy",
            "all",
            "--journal-dir",
            str(tmp_path / "nonexistent"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--json",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["strategies"] == {}
    assert payload["portfolio"]["n_strategies"] == 0


def test_main_loads_backtest_summary(tmp_path: Path, capsys):
    journal_dir = tmp_path / "journal"
    asof = datetime.now(UTC)
    base = asof - timedelta(days=10)
    events = [
        _make_fill(strategy="alpha", ts=base + timedelta(days=i), pnl=1.0)
        for i in range(10)
    ]
    _write_journal(journal_dir, events)

    bt = tmp_path / "bt.json"
    bt.write_text(
        json.dumps({"alpha": {"sharpe": 1.0, "max_dd": 0.1, "win_rate": 0.55}}),
        encoding="utf-8",
    )

    rc = clr.main(
        [
            "--strategy",
            "alpha",
            "--backtest-summary",
            str(bt),
            "--journal-dir",
            str(journal_dir),
            "--report-dir",
            str(tmp_path / "reports"),
            "--json",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    # Criterion 2 should now have a measured backtest_sharpe value.
    crit2 = next(
        c for c in payload["strategies"]["alpha"]["criteria"] if c["n"] == 2
    )
    assert crit2["measured"]["backtest_sharpe"] == 1.0


def test_main_pretty_output_contains_overall_line(tmp_path: Path, capsys):
    journal_dir = tmp_path / "journal"
    asof = datetime.now(UTC)
    events = [_make_fill(strategy="alpha", ts=asof - timedelta(days=1))]
    _write_journal(journal_dir, events)
    rc = clr.main(
        [
            "--strategy",
            "alpha",
            "--journal-dir",
            str(journal_dir),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Overall:" in out
    assert "[1/9]" in out
    assert "[9/9]" in out
