#!/usr/bin/env python3
"""Live-readiness checker.

Audits whether a strategy (or the whole portfolio) is ready to graduate from
paper to small real capital. Each of 9 gates returns PASS or FAIL with the
measured value and a reason. All 9 must PASS for promotion.

Usage:
  uv run python scripts/check_live_ready.py --strategy <name>
  uv run python scripts/check_live_ready.py --portfolio
  uv run python scripts/check_live_ready.py --strategy <name> --json
  uv run python scripts/check_live_ready.py --strategy <name> --asof 2026-05-01
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateAudit:
    gate_id: int
    name: str
    threshold: str
    actual: str | None
    passed: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "gate_id": self.gate_id,
            "name": self.name,
            "threshold": self.threshold,
            "actual": self.actual,
            "passed": self.passed,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    target: str
    asof: date
    gates: tuple[GateAudit, ...]
    all_passed: bool

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "asof": self.asof.isoformat(),
            "all_passed": self.all_passed,
            "gates": [g.to_dict() for g in self.gates],
        }


# ---------------------------------------------------------------------------
# Helpers (defensive: never raise on missing/corrupt data)
# ---------------------------------------------------------------------------


def _latest_backtest_metrics(strategy: str, repo: Path = REPO) -> dict | None:
    """Return parsed metrics.json from the latest backtest run, or None."""
    bt_root = repo / "backtests" / strategy
    if not bt_root.is_dir():
        return None
    runs = sorted(
        (p for p in bt_root.iterdir() if p.is_dir() and (p / "metrics.json").is_file()),
        key=lambda p: p.name,
    )
    if not runs:
        return None
    metrics_path = runs[-1] / "metrics.json"
    try:
        return json.loads(metrics_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _read_journal_records(asof: date, days: int, repo: Path = REPO) -> list[dict]:
    """Read journal records over the last `days` ending at `asof`. Defensive."""
    journal_dir = repo / "journal"
    if not journal_dir.is_dir():
        return []
    records: list[dict] = []
    for offset in range(days):
        d = asof - timedelta(days=offset)
        path = journal_dir / f"{d.isoformat()}.jsonl"
        if not path.is_file():
            continue
        try:
            for raw_line in path.read_text().splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return records


def _filter_strategy(records: list[dict], strategy: str) -> list[dict]:
    """Keep only records that mention this strategy (best-effort)."""
    out = []
    for r in records:
        if r.get("strategy") == strategy:
            out.append(r)
            continue
        # fall through: also accept if cycle_id contains the strategy tag
        cycle = r.get("cycle_id", "")
        if isinstance(cycle, str) and strategy in cycle:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Individual gate auditors. Each returns a GateAudit and never raises.
# ---------------------------------------------------------------------------


def _gate_paper_duration(
    strategy: str, asof: date, records: list[dict]
) -> GateAudit:
    """Gate 1: forward paper duration >= 6 months."""
    strat_records = _filter_strategy(records, strategy)
    if not strat_records:
        return GateAudit(
            gate_id=1,
            name="Forward paper duration >= 6 months",
            threshold=">=6 months of forward paper trades",
            actual=None,
            passed=False,
            reason="no live data yet -- paper validation pending",
        )
    timestamps: list[datetime] = []
    for r in strat_records:
        ts = r.get("ts")
        if not isinstance(ts, str):
            continue
        try:
            timestamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except ValueError:
            continue
    if not timestamps:
        return GateAudit(
            gate_id=1,
            name="Forward paper duration >= 6 months",
            threshold=">=6 months of forward paper trades",
            actual=None,
            passed=False,
            reason="no parsable timestamps in journal",
        )
    span_days = (max(timestamps).date() - min(timestamps).date()).days
    passed = span_days >= 180
    return GateAudit(
        gate_id=1,
        name="Forward paper duration >= 6 months",
        threshold=">=180 days span",
        actual=f"{span_days} days",
        passed=passed,
        reason=("span sufficient" if passed else "needs more forward-paper time"),
    )


def _gate_live_sharpe(
    strategy: str, asof: date, records: list[dict], backtest: dict | None
) -> GateAudit:
    """Gate 2: live Sharpe >= 0.7 * backtest Sharpe."""
    if backtest is None or "sharpe" not in backtest:
        return GateAudit(
            gate_id=2,
            name="Live Sharpe >= 0.7 x backtest Sharpe",
            threshold="live_sharpe >= 0.7 * backtest_sharpe",
            actual=None,
            passed=False,
            reason="no backtest metrics.json found",
        )
    live_records = _filter_strategy(records, strategy)
    live_sharpe = None
    for r in reversed(live_records):
        if "live_sharpe" in r:
            try:
                live_sharpe = float(r["live_sharpe"])
                break
            except (TypeError, ValueError):
                continue
    if live_sharpe is None:
        return GateAudit(
            gate_id=2,
            name="Live Sharpe >= 0.7 x backtest Sharpe",
            threshold="live_sharpe >= 0.7 * backtest_sharpe",
            actual=None,
            passed=False,
            reason="no live data yet -- paper validation pending",
        )
    bt_sharpe = float(backtest["sharpe"])
    threshold = 0.7 * bt_sharpe
    passed = live_sharpe >= threshold
    return GateAudit(
        gate_id=2,
        name="Live Sharpe >= 0.7 x backtest Sharpe",
        threshold=f">= {threshold:.3f} (0.7 x {bt_sharpe:.3f})",
        actual=f"{live_sharpe:.3f}",
        passed=passed,
        reason=("ratio acceptable" if passed else "live Sharpe degraded too much"),
    )


def _gate_live_max_dd(
    strategy: str, asof: date, records: list[dict], backtest: dict | None
) -> GateAudit:
    """Gate 3: live max DD <= 1.3 * backtest max DD."""
    if backtest is None or "max_dd" not in backtest:
        return GateAudit(
            gate_id=3,
            name="Live max DD <= 1.3 x backtest max DD",
            threshold="live_max_dd <= 1.3 * backtest_max_dd",
            actual=None,
            passed=False,
            reason="no backtest metrics.json found",
        )
    live_records = _filter_strategy(records, strategy)
    live_dd = None
    for r in reversed(live_records):
        if "live_max_dd" in r:
            try:
                live_dd = float(r["live_max_dd"])
                break
            except (TypeError, ValueError):
                continue
    if live_dd is None:
        return GateAudit(
            gate_id=3,
            name="Live max DD <= 1.3 x backtest max DD",
            threshold="live_max_dd <= 1.3 * backtest_max_dd",
            actual=None,
            passed=False,
            reason="no live data yet -- paper validation pending",
        )
    bt_dd = float(backtest["max_dd"])
    threshold = 1.3 * bt_dd
    passed = live_dd <= threshold
    return GateAudit(
        gate_id=3,
        name="Live max DD <= 1.3 x backtest max DD",
        threshold=f"<= {threshold:.4f} (1.3 x {bt_dd:.4f})",
        actual=f"{live_dd:.4f}",
        passed=passed,
        reason=(
            "drawdown within tolerance"
            if passed
            else "live drawdown exceeds modeled tail"
        ),
    )


def _gate_trade_count(
    strategy: str, asof: date, records: list[dict]
) -> GateAudit:
    """Gate 4: >= 150 trades total."""
    live_records = _filter_strategy(records, strategy)
    n = sum(
        1
        for r in live_records
        if r.get("event") in ("submit_dry_run", "submit", "fill")
    )
    passed = n >= 150
    return GateAudit(
        gate_id=4,
        name="Trade count >= 150",
        threshold=">=150 fills/submits",
        actual=str(n),
        passed=passed,
        reason=(
            "sample size adequate"
            if passed
            else "insufficient trades -- estimates dominated by noise"
        ),
    )


def _gate_slippage_mae(
    strategy: str, asof: date, records: list[dict], backtest: dict | None
) -> GateAudit:
    """Gate 5: slippage MAE <= 5 bps vs backtest assumption."""
    live_records = _filter_strategy(records, strategy)
    bps_values: list[float] = []
    for r in live_records:
        v = r.get("slippage_bps")
        if v is None:
            continue
        try:
            bps_values.append(abs(float(v)))
        except (TypeError, ValueError):
            continue
    if not bps_values:
        return GateAudit(
            gate_id=5,
            name="Slippage MAE <= 5 bps",
            threshold="MAE <= 5 bps",
            actual=None,
            passed=False,
            reason="no slippage_bps records in journal yet",
        )
    mae = sum(bps_values) / len(bps_values)
    passed = mae <= 5.0
    return GateAudit(
        gate_id=5,
        name="Slippage MAE <= 5 bps",
        threshold="MAE <= 5 bps",
        actual=f"{mae:.2f} bps",
        passed=passed,
        reason=(
            "slippage within modeled assumption"
            if passed
            else "live slippage exceeds backtest assumption"
        ),
    )


def _gate_risk_breaches(
    strategy: str, asof: date, records: list[dict]
) -> GateAudit:
    """Gate 6: 0 risk-cap breaches in last 90 days."""
    if not records or not _filter_strategy(records, strategy):
        return GateAudit(
            gate_id=6,
            name="0 risk-cap breaches in last 90 days",
            threshold="==0",
            actual=None,
            passed=False,
            reason="no live data yet -- paper validation pending",
        )
    breaches = 0
    for r in records:
        if r.get("gate") == "risk" and r.get("decision") == "REJECT":
            # Filter by strategy if the record names one; otherwise check cycle.
            rec_strat = r.get("strategy")
            cycle = r.get("cycle_id", "")
            if rec_strat is None and (
                not isinstance(cycle, str) or strategy not in cycle
            ):
                continue
            breaches += 1
    passed = breaches == 0
    return GateAudit(
        gate_id=6,
        name="0 risk-cap breaches in last 90 days",
        threshold="==0",
        actual=str(breaches),
        passed=passed,
        reason=(
            "no risk REJECTs"
            if passed
            else f"{breaches} risk REJECT records in last 90 days"
        ),
    )


def _gate_coherence(
    strategy: str, asof: date, records30: list[dict], backtest: dict | None
) -> GateAudit:
    """Gate 7: live_WR / backtest_WR >= 0.5 in last 30 days."""
    if backtest is None or "win_rate" not in backtest:
        return GateAudit(
            gate_id=7,
            name="Coherence (live_WR / backtest_WR) >= 0.5 (30d)",
            threshold=">=0.5",
            actual=None,
            passed=False,
            reason="no backtest win_rate available",
        )
    live_records = _filter_strategy(records30, strategy)
    fills = [r for r in live_records if r.get("event") == "fill"]
    if not fills:
        return GateAudit(
            gate_id=7,
            name="Coherence (live_WR / backtest_WR) >= 0.5 (30d)",
            threshold=">=0.5",
            actual=None,
            passed=False,
            reason="no live fills in last 30 days",
        )
    wins = 0
    counted = 0
    for r in fills:
        pnl = r.get("pnl")
        if pnl is None:
            continue
        try:
            counted += 1
            if float(pnl) > 0:
                wins += 1
        except (TypeError, ValueError):
            continue
    if counted == 0:
        return GateAudit(
            gate_id=7,
            name="Coherence (live_WR / backtest_WR) >= 0.5 (30d)",
            threshold=">=0.5",
            actual=None,
            passed=False,
            reason="no fills carry pnl in last 30 days",
        )
    live_wr = wins / counted
    bt_wr = float(backtest["win_rate"])
    if bt_wr <= 0:
        return GateAudit(
            gate_id=7,
            name="Coherence (live_WR / backtest_WR) >= 0.5 (30d)",
            threshold=">=0.5",
            actual=f"live_WR={live_wr:.3f}",
            passed=False,
            reason="backtest win_rate is zero or negative",
        )
    coherence = live_wr / bt_wr
    passed = coherence >= 0.5
    return GateAudit(
        gate_id=7,
        name="Coherence (live_WR / backtest_WR) >= 0.5 (30d)",
        threshold=">=0.5",
        actual=f"{coherence:.3f}",
        passed=passed,
        reason=(
            "win-rate ratio coherent"
            if passed
            else "recent win-rate has drifted below half of backtest"
        ),
    )


def _gate_drift_halts(
    strategy: str, asof: date, records30: list[dict]
) -> GateAudit:
    """Gate 8: 0 drift detector halts in last 30 days."""
    if not records30 or not _filter_strategy(records30, strategy):
        return GateAudit(
            gate_id=8,
            name="0 drift detector halts in last 30 days",
            threshold="==0",
            actual=None,
            passed=False,
            reason="no live data yet -- paper validation pending",
        )
    halts = 0
    for r in records30:
        evt = r.get("event") or r.get("gate")
        if evt in ("drift_halt", "drift_detector_halt", "drift"):
            if r.get("decision") == "HALT" or r.get("event") == "drift_halt":
                halts += 1
                continue
        if evt == "drift" and r.get("status") == "halt":
            halts += 1
    passed = halts == 0
    return GateAudit(
        gate_id=8,
        name="0 drift detector halts in last 30 days",
        threshold="==0",
        actual=str(halts),
        passed=passed,
        reason=(
            "no drift halts"
            if passed
            else f"{halts} drift halt(s) in last 30 days"
        ),
    )


def _gate_pairwise_correlation(
    strategy: str, asof: date, records: list[dict]
) -> GateAudit:
    """Gate 9: pairwise correlation with all live strategies <= 0.7."""
    corrs: dict[str, float] = {}
    for r in records:
        if r.get("event") == "pairwise_corr" and r.get("strategy") == strategy:
            other = r.get("other")
            v = r.get("corr")
            if not isinstance(other, str):
                continue
            try:
                corrs[other] = float(v)
            except (TypeError, ValueError):
                continue
    if not corrs:
        return GateAudit(
            gate_id=9,
            name="Pairwise correlation with all live strategies <= 0.7",
            threshold="<=0.7 vs every live strategy",
            actual=None,
            passed=False,
            reason="no pairwise_corr records yet",
        )
    worst_other = max(corrs, key=lambda k: corrs[k])
    worst_v = corrs[worst_other]
    passed = worst_v <= 0.7
    return GateAudit(
        gate_id=9,
        name="Pairwise correlation with all live strategies <= 0.7",
        threshold="<=0.7 vs every live strategy",
        actual=f"max={worst_v:.3f} vs {worst_other}",
        passed=passed,
        reason=(
            "diversification adequate"
            if passed
            else f"correlation {worst_v:.3f} with {worst_other} too high"
        ),
    )


# ---------------------------------------------------------------------------
# Public auditors
# ---------------------------------------------------------------------------


def audit_strategy(
    strategy: str, asof: date, repo: Path = REPO
) -> ReadinessResult:
    """Run all 9 gates against `strategy`. Defensive: never raises."""
    backtest = _latest_backtest_metrics(strategy, repo=repo)
    # Pull three windows: 365d (gate 1 needs a wide window to measure span),
    # 90d (gates 4, 6, 9 and the live aggregates for gates 2, 3, 5),
    # 30d (gates 7, 8).
    records365 = _read_journal_records(asof, days=365, repo=repo)
    records90 = _read_journal_records(asof, days=90, repo=repo)
    records30 = _read_journal_records(asof, days=30, repo=repo)

    gates: tuple[GateAudit, ...] = (
        _gate_paper_duration(strategy, asof, records365),
        _gate_live_sharpe(strategy, asof, records90, backtest),
        _gate_live_max_dd(strategy, asof, records90, backtest),
        _gate_trade_count(strategy, asof, records90),
        _gate_slippage_mae(strategy, asof, records90, backtest),
        _gate_risk_breaches(strategy, asof, records90),
        _gate_coherence(strategy, asof, records30, backtest),
        _gate_drift_halts(strategy, asof, records30),
        _gate_pairwise_correlation(strategy, asof, records90),
    )
    return ReadinessResult(
        target=strategy,
        asof=asof,
        gates=gates,
        all_passed=all(g.passed for g in gates),
    )


def audit_portfolio(asof: date, repo: Path = REPO) -> ReadinessResult:
    """Run the audit for every strategy under backtests/ and aggregate.

    The returned result has gates that are the union of per-strategy audits,
    each gate prefixed with its strategy tag. all_passed iff every per-strategy
    gate passed.
    """
    bt_root = repo / "backtests"
    strategies: list[str] = []
    if bt_root.is_dir():
        strategies = sorted(
            p.name for p in bt_root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
    aggregated: list[GateAudit] = []
    if not strategies:
        aggregated.append(
            GateAudit(
                gate_id=0,
                name="No strategies discovered",
                threshold="backtests/<strategy>/ must exist",
                actual=None,
                passed=False,
                reason="no strategy directories under backtests/",
            )
        )
    for strat in strategies:
        sub = audit_strategy(strat, asof, repo=repo)
        for g in sub.gates:
            aggregated.append(
                GateAudit(
                    gate_id=g.gate_id,
                    name=f"[{strat}] {g.name}",
                    threshold=g.threshold,
                    actual=g.actual,
                    passed=g.passed,
                    reason=g.reason,
                )
            )
    return ReadinessResult(
        target="portfolio",
        asof=asof,
        gates=tuple(aggregated),
        all_passed=all(g.passed for g in aggregated),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def print_human_readable(result: ReadinessResult) -> None:
    print(f"Live-readiness audit: {result.target}  asof={result.asof.isoformat()}")
    print("-" * 78)
    for g in result.gates:
        marker = "PASS" if g.passed else "FAIL"
        actual = g.actual if g.actual is not None else "n/a"
        print(f"  [{marker}] gate {g.gate_id}: {g.name}")
        print(f"         threshold: {g.threshold}")
        print(f"         actual:    {actual}")
        print(f"         reason:    {g.reason}")
    print("-" * 78)
    summary = "ALL PASS" if result.all_passed else "BLOCKED"
    print(f"Overall: {summary}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Audit whether a strategy is ready to graduate from paper to real capital."
    )
    p.add_argument("--strategy", help="Strategy tag to audit")
    p.add_argument(
        "--portfolio", action="store_true", help="Audit whole portfolio"
    )
    p.add_argument("--asof", help="YYYY-MM-DD, default today")
    p.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Output machine-readable JSON",
    )
    args = p.parse_args()

    asof = date.fromisoformat(args.asof) if args.asof else date.today()

    if args.strategy:
        result = audit_strategy(args.strategy, asof)
    elif args.portfolio:
        result = audit_portfolio(asof)
    else:
        p.error("must specify --strategy or --portfolio")
        return 2  # unreachable, p.error exits

    if args.emit_json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print_human_readable(result)
    return 0 if result.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
