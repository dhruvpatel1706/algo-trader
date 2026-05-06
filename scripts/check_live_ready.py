#!/usr/bin/env python3
"""Phase 9 live-readiness gate.

Read-only audit. Never touches state. Run weekly during paper validation.

This is the gate between paper and live: per project plan (Phase 9), no
strategy moves real money without passing all 9 criteria below. The script
reports PASS / FAIL / INDETERMINATE per criterion, never exits non-zero
(it is a report, not a CI gate).

The 9 criteria
--------------
1. Forward paper duration:        >= 180 days since first trade
2. Live Sharpe vs backtest:        live_sharpe >= 0.7 * backtest_sharpe
3. Live max DD vs backtest:        live_dd <= 1.3 * backtest_dd
4. Total trades (per strategy):    >= 150
5. Slippage MAE (live vs ideal):   <= 5 bps
6. Risk-cap breaches in journal:   0 in last 90 days
7. Coherence (live_WR/backtest_WR): >= 0.5 in last 30 days
8. Drift detector halt count:      0 in last 30 days
9. Pairwise correlation w/ others: <= 0.7 alarm threshold

Note on (6): "breach" means a cap *violation* that should not have happened
(cap_breach_alert event), NOT a refusal (which is the cap doing its job).
For v1 we count `event=cap_breach_alert` records; refusals are healthy.

The script accepts an optional ``--backtest-summary <json>`` file mapping
``strategy_name -> {sharpe, max_dd, win_rate, n_trades}``. Without it,
criteria 2 / 3 / 7 are INDETERMINATE because they need the backtest baseline.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

logger = logging.getLogger("check_live_ready")

CriterionResult = Literal["PASS", "FAIL", "INDETERMINATE"]
OverallResult = Literal["READY", "NOT_READY", "INDETERMINATE"]

# Thresholds — single source of truth, also referenced by docs/live_readiness.md.
THRESHOLD_FORWARD_DAYS: int = 180
THRESHOLD_SHARPE_RATIO: float = 0.7
THRESHOLD_DD_RATIO: float = 1.3
THRESHOLD_MIN_TRADES: int = 150
THRESHOLD_SLIPPAGE_BPS: float = 5.0
THRESHOLD_RISK_BREACH_WINDOW_DAYS: int = 90
THRESHOLD_COHERENCE_WINDOW_DAYS: int = 30
THRESHOLD_COHERENCE_RATIO: float = 0.5
THRESHOLD_DRIFT_WINDOW_DAYS: int = 30
THRESHOLD_CORRELATION: float = 0.7
CORRELATION_LOOKBACK_DAYS: int = 63

TRADING_DAYS_PER_YEAR: int = 252


# --------------------------------------------------------------------------- #
# Data classes                                                                #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class CriterionCheck:
    """One criterion's outcome. ``measured`` carries raw numbers for the report."""

    n: int
    name: str
    result: CriterionResult
    detail: str
    measured: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StrategyReport:
    """All 9 criteria for one strategy + an overall verdict."""

    strategy: str
    asof: str
    criteria: list[CriterionCheck]
    overall: OverallResult
    blocking: list[int]


# --------------------------------------------------------------------------- #
# Journal I/O                                                                 #
# --------------------------------------------------------------------------- #


def _read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    """Parse one JSONL file. Skip blank or malformed lines silently."""
    if not path.exists() or not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_all_journal_events(journal_dir: Path) -> list[dict[str, Any]]:
    """Read every YYYY-MM-DD.jsonl file in ``journal_dir``. Returns time-sorted events."""
    if not journal_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(journal_dir.glob("*.jsonl")):
        events.extend(_read_jsonl_file(path))

    def _ts(ev: dict[str, Any]) -> str:
        ts = ev.get("ts", "")
        return ts if isinstance(ts, str) else ""

    events.sort(key=_ts)
    return events


def _parse_ts(ev: dict[str, Any]) -> datetime | None:
    """Parse ev['ts'] (ISO8601) into a UTC-aware datetime, or None if unparseable."""
    raw = ev.get("ts")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _filter_strategy(events: list[dict[str, Any]], strategy: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("strategy") == strategy]


def _list_strategies(events: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for e in events:
        s = e.get("strategy")
        if isinstance(s, str) and s:
            seen.add(s)
    return sorted(seen)


# --------------------------------------------------------------------------- #
# Trade extraction helpers                                                    #
# --------------------------------------------------------------------------- #


def _is_fill(ev: dict[str, Any]) -> bool:
    return ev.get("event") in {"fill", "partial_fill"}


def _fill_price(ev: dict[str, Any]) -> float | None:
    for k in ("fill_price", "filled_avg_price", "price"):
        v = ev.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _intended_price(ev: dict[str, Any]) -> float | None:
    for k in ("intended_price", "limit_price", "expected_price"):
        v = ev.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _trade_pnl(ev: dict[str, Any]) -> float | None:
    """Realized PnL for a closing fill, if journaled. Falls back to ``realized_pnl``."""
    for k in ("realized_pnl", "pnl", "trade_pnl"):
        v = ev.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _build_daily_pnl_series(
    events: list[dict[str, Any]], strategy: str
) -> pd.Series:
    """Sum realized PnL per UTC day across fills with strategy=<strategy>.

    Returns an empty Series if no realized PnL is journaled.
    """
    rows: list[tuple[datetime, float]] = []
    for ev in events:
        if ev.get("strategy") != strategy or not _is_fill(ev):
            continue
        pnl = _trade_pnl(ev)
        if pnl is None:
            continue
        ts = _parse_ts(ev)
        if ts is None:
            continue
        rows.append((ts, pnl))
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["ts", "pnl"])
    df["date"] = df["ts"].dt.floor("D")
    return df.groupby("date")["pnl"].sum().sort_index()


# --------------------------------------------------------------------------- #
# Criterion implementations                                                   #
# --------------------------------------------------------------------------- #


def check_criterion_1_forward_paper_duration(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    asof: datetime,
    threshold_days: int = THRESHOLD_FORWARD_DAYS,
) -> CriterionCheck:
    """Days between earliest journaled event for the strategy and ``asof``."""
    strat_events = _filter_strategy(events, strategy)
    earliest: datetime | None = None
    for ev in strat_events:
        ts = _parse_ts(ev)
        if ts is None:
            continue
        if earliest is None or ts < earliest:
            earliest = ts
    if earliest is None:
        return CriterionCheck(
            n=1,
            name="forward_paper_duration",
            result="INDETERMINATE",
            detail=f"no journaled events for strategy={strategy}",
            measured={"days": None, "threshold": threshold_days},
        )
    days = (asof - earliest).days
    result: CriterionResult = "PASS" if days >= threshold_days else "FAIL"
    return CriterionCheck(
        n=1,
        name="forward_paper_duration",
        result=result,
        detail=f"{days} days since first trade, threshold >= {threshold_days}",
        measured={
            "days": days,
            "threshold": threshold_days,
            "first_trade_ts": earliest.isoformat(),
        },
    )


def _annualized_sharpe(daily_pnl: pd.Series) -> float | None:
    """Naive Sharpe on daily realized PnL. None if insufficient data."""
    if len(daily_pnl) < 2:
        return None
    std = float(daily_pnl.std(ddof=1))
    if std <= 0 or not math.isfinite(std):
        return None
    mean = float(daily_pnl.mean())
    return mean / std * math.sqrt(TRADING_DAYS_PER_YEAR)


def check_criterion_2_live_sharpe_vs_backtest(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    backtest_summary: dict[str, Any] | None,
    threshold_ratio: float = THRESHOLD_SHARPE_RATIO,
) -> CriterionCheck:
    """live_sharpe >= threshold_ratio * backtest_sharpe."""
    daily = _build_daily_pnl_series(events, strategy)
    live_sharpe = _annualized_sharpe(daily)

    bt_entry: dict[str, Any] | None = None
    if backtest_summary is not None:
        raw = backtest_summary.get(strategy)
        if isinstance(raw, dict):
            bt_entry = raw
    bt_sharpe = None
    if bt_entry is not None:
        try:
            bt_sharpe = float(bt_entry["sharpe"])
        except (KeyError, TypeError, ValueError):
            bt_sharpe = None

    if live_sharpe is None or bt_sharpe is None:
        return CriterionCheck(
            n=2,
            name="live_sharpe_vs_backtest",
            result="INDETERMINATE",
            detail=(
                f"live_sharpe={live_sharpe} backtest_sharpe={bt_sharpe} "
                "(need both to compute ratio)"
            ),
            measured={
                "live_sharpe": live_sharpe,
                "backtest_sharpe": bt_sharpe,
                "threshold_ratio": threshold_ratio,
            },
        )
    if bt_sharpe == 0:
        return CriterionCheck(
            n=2,
            name="live_sharpe_vs_backtest",
            result="INDETERMINATE",
            detail="backtest_sharpe is zero — ratio undefined",
            measured={
                "live_sharpe": live_sharpe,
                "backtest_sharpe": bt_sharpe,
                "threshold_ratio": threshold_ratio,
            },
        )
    ratio = live_sharpe / bt_sharpe
    result: CriterionResult = "PASS" if ratio >= threshold_ratio else "FAIL"
    return CriterionCheck(
        n=2,
        name="live_sharpe_vs_backtest",
        result=result,
        detail=(
            f"live={live_sharpe:.2f}, backtest={bt_sharpe:.2f}, "
            f"ratio {ratio:.2f} {'>=' if result == 'PASS' else '<'} {threshold_ratio:.2f}"
        ),
        measured={
            "live_sharpe": live_sharpe,
            "backtest_sharpe": bt_sharpe,
            "ratio": ratio,
            "threshold_ratio": threshold_ratio,
        },
    )


def _max_drawdown_from_daily_pnl(daily_pnl: pd.Series) -> float | None:
    """Max drawdown of the cumulative-PnL equity curve. Returns positive fraction.

    None if the curve never reaches a positive peak (so a relative DD is meaningless).
    """
    if daily_pnl.empty:
        return None
    equity = daily_pnl.cumsum()
    peak = equity.cummax()
    valid = peak > 0
    if not valid.any():
        return None
    drawdown = (peak - equity) / peak
    drawdown = drawdown[valid]
    if drawdown.empty:
        return None
    md = float(drawdown.max())
    if not math.isfinite(md):
        return None
    return md


def check_criterion_3_live_dd_vs_backtest(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    backtest_summary: dict[str, Any] | None,
    threshold_ratio: float = THRESHOLD_DD_RATIO,
) -> CriterionCheck:
    """live_dd <= threshold_ratio * backtest_dd."""
    daily = _build_daily_pnl_series(events, strategy)
    live_dd = _max_drawdown_from_daily_pnl(daily)

    bt_entry: dict[str, Any] | None = None
    if backtest_summary is not None:
        raw = backtest_summary.get(strategy)
        if isinstance(raw, dict):
            bt_entry = raw
    bt_dd = None
    if bt_entry is not None:
        try:
            bt_dd = float(bt_entry["max_dd"])
        except (KeyError, TypeError, ValueError):
            bt_dd = None

    if live_dd is None or bt_dd is None:
        return CriterionCheck(
            n=3,
            name="live_max_dd_vs_backtest",
            result="INDETERMINATE",
            detail=(
                f"live_dd={live_dd} backtest_dd={bt_dd} (need both to compute ratio)"
            ),
            measured={
                "live_dd": live_dd,
                "backtest_dd": bt_dd,
                "threshold_ratio": threshold_ratio,
            },
        )
    if bt_dd == 0:
        # Can't form a ratio against zero. Pass iff live also ~zero.
        result: CriterionResult = "PASS" if live_dd <= 0 else "FAIL"
        return CriterionCheck(
            n=3,
            name="live_max_dd_vs_backtest",
            result=result,
            detail=f"backtest_dd is zero; live_dd={live_dd:.4f}",
            measured={
                "live_dd": live_dd,
                "backtest_dd": bt_dd,
                "threshold_ratio": threshold_ratio,
            },
        )
    ratio = live_dd / bt_dd
    result = "PASS" if ratio <= threshold_ratio else "FAIL"
    return CriterionCheck(
        n=3,
        name="live_max_dd_vs_backtest",
        result=result,
        detail=(
            f"live={live_dd:.4f}, backtest={bt_dd:.4f}, "
            f"ratio {ratio:.2f} {'<=' if result == 'PASS' else '>'} {threshold_ratio:.2f}"
        ),
        measured={
            "live_dd": live_dd,
            "backtest_dd": bt_dd,
            "ratio": ratio,
            "threshold_ratio": threshold_ratio,
        },
    )


def check_criterion_4_total_trades(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    threshold: int = THRESHOLD_MIN_TRADES,
) -> CriterionCheck:
    """Count fills (full and partial) for the strategy across all journaled history."""
    n = sum(1 for ev in events if ev.get("strategy") == strategy and _is_fill(ev))
    result: CriterionResult = "PASS" if n >= threshold else "FAIL"
    return CriterionCheck(
        n=4,
        name="total_trades",
        result=result,
        detail=f"{n} {'>=' if result == 'PASS' else '<'} {threshold}",
        measured={"n_trades": n, "threshold": threshold},
    )


def check_criterion_5_slippage_mae(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    threshold_bps: float = THRESHOLD_SLIPPAGE_BPS,
) -> CriterionCheck:
    """Mean absolute slippage in bps across fills with both fill_price and intended_price."""
    abs_bps: list[float] = []
    for ev in events:
        if ev.get("strategy") != strategy or not _is_fill(ev):
            continue
        fp = _fill_price(ev)
        ip = _intended_price(ev)
        if fp is None or ip is None or ip == 0:
            continue
        abs_bps.append(abs(fp - ip) / abs(ip) * 10_000.0)
    if not abs_bps:
        return CriterionCheck(
            n=5,
            name="slippage_mae",
            result="INDETERMINATE",
            detail="no fills with both fill_price and intended_price journaled",
            measured={"mae_bps": None, "n_fills": 0, "threshold_bps": threshold_bps},
        )
    mae = float(np.mean(abs_bps))
    result: CriterionResult = "PASS" if mae <= threshold_bps else "FAIL"
    return CriterionCheck(
        n=5,
        name="slippage_mae",
        result=result,
        detail=(
            f"{mae:.2f} bps {'<=' if result == 'PASS' else '>'} {threshold_bps:.1f} bps"
        ),
        measured={"mae_bps": mae, "n_fills": len(abs_bps), "threshold_bps": threshold_bps},
    )


def check_criterion_6_risk_cap_breaches(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    asof: datetime,
    window_days: int = THRESHOLD_RISK_BREACH_WINDOW_DAYS,
) -> CriterionCheck:
    """Count ``cap_breach_alert`` events for this strategy in the last ``window_days``.

    A *breach* is a violation that should not have happened (a position got
    through that exceeded a cap). Refusals (caps doing their job) are healthy
    and are NOT counted here.
    """
    cutoff = asof - timedelta(days=window_days)
    n = 0
    for ev in events:
        if ev.get("strategy") != strategy:
            continue
        if ev.get("event") != "cap_breach_alert":
            continue
        ts = _parse_ts(ev)
        if ts is None or ts < cutoff:
            continue
        n += 1
    result: CriterionResult = "PASS" if n == 0 else "FAIL"
    return CriterionCheck(
        n=6,
        name="risk_cap_breaches",
        result=result,
        detail=f"{n} cap_breach_alert events in last {window_days}d",
        measured={"n_breaches": n, "window_days": window_days},
    )


def _win_rate(events: list[dict[str, Any]], strategy: str, since: datetime | None) -> float | None:
    """Fraction of fills with realized PnL > 0. None if no fills with PnL."""
    pnls: list[float] = []
    for ev in events:
        if ev.get("strategy") != strategy or not _is_fill(ev):
            continue
        if since is not None:
            ts = _parse_ts(ev)
            if ts is None or ts < since:
                continue
        pnl = _trade_pnl(ev)
        if pnl is None:
            continue
        pnls.append(pnl)
    if not pnls:
        return None
    return float(sum(1 for p in pnls if p > 0) / len(pnls))


def check_criterion_7_coherence(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    backtest_summary: dict[str, Any] | None,
    asof: datetime,
    window_days: int = THRESHOLD_COHERENCE_WINDOW_DAYS,
    threshold_ratio: float = THRESHOLD_COHERENCE_RATIO,
) -> CriterionCheck:
    """live_WR (last 30 days) / backtest_WR >= threshold_ratio."""
    since = asof - timedelta(days=window_days)
    live_wr = _win_rate(events, strategy, since=since)

    bt_entry: dict[str, Any] | None = None
    if backtest_summary is not None:
        raw = backtest_summary.get(strategy)
        if isinstance(raw, dict):
            bt_entry = raw
    bt_wr = None
    if bt_entry is not None:
        try:
            bt_wr = float(bt_entry["win_rate"])
        except (KeyError, TypeError, ValueError):
            bt_wr = None

    if live_wr is None or bt_wr is None:
        return CriterionCheck(
            n=7,
            name="coherence",
            result="INDETERMINATE",
            detail=(
                f"live_wr_30d={live_wr} backtest_wr={bt_wr} (need both to compute ratio)"
            ),
            measured={
                "live_win_rate_30d": live_wr,
                "backtest_win_rate": bt_wr,
                "threshold_ratio": threshold_ratio,
                "window_days": window_days,
            },
        )
    if bt_wr == 0:
        return CriterionCheck(
            n=7,
            name="coherence",
            result="INDETERMINATE",
            detail="backtest_win_rate is zero — ratio undefined",
            measured={
                "live_win_rate_30d": live_wr,
                "backtest_win_rate": bt_wr,
                "threshold_ratio": threshold_ratio,
                "window_days": window_days,
            },
        )
    ratio = live_wr / bt_wr
    result: CriterionResult = "PASS" if ratio >= threshold_ratio else "FAIL"
    return CriterionCheck(
        n=7,
        name="coherence",
        result=result,
        detail=(
            f"live_wr_30d={live_wr:.2f}, backtest_wr={bt_wr:.2f}, "
            f"ratio {ratio:.2f} {'>=' if result == 'PASS' else '<'} {threshold_ratio:.2f}"
        ),
        measured={
            "live_win_rate_30d": live_wr,
            "backtest_win_rate": bt_wr,
            "ratio": ratio,
            "threshold_ratio": threshold_ratio,
            "window_days": window_days,
        },
    )


def check_criterion_8_drift_halts(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    asof: datetime,
    window_days: int = THRESHOLD_DRIFT_WINDOW_DAYS,
) -> CriterionCheck:
    """Count ``drift_halt`` events for the strategy in last ``window_days``."""
    cutoff = asof - timedelta(days=window_days)
    n = 0
    for ev in events:
        if ev.get("strategy") != strategy:
            continue
        if ev.get("event") != "drift_halt":
            continue
        ts = _parse_ts(ev)
        if ts is None or ts < cutoff:
            continue
        n += 1
    result: CriterionResult = "PASS" if n == 0 else "FAIL"
    return CriterionCheck(
        n=8,
        name="drift_halts",
        result=result,
        detail=f"{n} drift_halt events in last {window_days}d",
        measured={"n_halts": n, "window_days": window_days},
    )


def check_criterion_9_pairwise_correlation(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    other_live_strategies: list[str],
    asof: datetime,
    lookback_days: int = CORRELATION_LOOKBACK_DAYS,
    threshold: float = THRESHOLD_CORRELATION,
) -> CriterionCheck:
    """Max |Pearson correlation| of daily PnL between this strategy and any other."""
    others = [s for s in other_live_strategies if s != strategy]
    if not others:
        return CriterionCheck(
            n=9,
            name="pairwise_correlation",
            result="INDETERMINATE",
            detail="no other live strategies to compare against",
            measured={
                "max_corr": None,
                "n_other_strategies": 0,
                "threshold": threshold,
                "lookback_days": lookback_days,
            },
        )

    cutoff = (asof - timedelta(days=lookback_days)).replace(tzinfo=UTC)
    cutoff_ts = pd.Timestamp(cutoff)

    def _filter_window(s: pd.Series) -> pd.Series:
        """Trim to the lookback window, tolerating empty / non-datetime indexes."""
        if s.empty:
            return s
        if not isinstance(s.index, pd.DatetimeIndex):
            return s.iloc[0:0]
        return s[s.index >= cutoff_ts]

    base = _filter_window(_build_daily_pnl_series(events, strategy))
    if len(base) < 2:
        return CriterionCheck(
            n=9,
            name="pairwise_correlation",
            result="INDETERMINATE",
            detail=f"insufficient daily-PnL history for {strategy} in last {lookback_days}d",
            measured={
                "max_corr": None,
                "n_other_strategies": len(others),
                "threshold": threshold,
                "lookback_days": lookback_days,
            },
        )

    pair_corrs: dict[str, float] = {}
    insufficient: list[str] = []
    for other in others:
        s = _filter_window(_build_daily_pnl_series(events, other))
        joined = pd.concat([base.rename("a"), s.rename("b")], axis=1).dropna()
        if len(joined) < 2:
            insufficient.append(other)
            continue
        if joined["a"].std(ddof=1) == 0 or joined["b"].std(ddof=1) == 0:
            insufficient.append(other)
            continue
        c = float(joined["a"].corr(joined["b"]))
        if not math.isfinite(c):
            insufficient.append(other)
            continue
        pair_corrs[other] = c

    if not pair_corrs:
        return CriterionCheck(
            n=9,
            name="pairwise_correlation",
            result="INDETERMINATE",
            detail=(
                f"insufficient overlapping data with other strategies: "
                f"{', '.join(insufficient) or 'none'}"
            ),
            measured={
                "max_corr": None,
                "n_other_strategies": len(others),
                "pair_corrs": {},
                "insufficient": insufficient,
                "threshold": threshold,
                "lookback_days": lookback_days,
            },
        )

    max_other = max(pair_corrs.items(), key=lambda kv: abs(kv[1]))
    max_abs = abs(max_other[1])
    result: CriterionResult = "PASS" if max_abs <= threshold else "FAIL"
    return CriterionCheck(
        n=9,
        name="pairwise_correlation",
        result=result,
        detail=(
            f"max |corr| = {max_abs:.2f} with {max_other[0]} "
            f"{'<=' if result == 'PASS' else '>'} {threshold:.2f}"
        ),
        measured={
            "max_corr": max_other[1],
            "max_corr_with": max_other[0],
            "pair_corrs": pair_corrs,
            "n_other_strategies": len(others),
            "insufficient": insufficient,
            "threshold": threshold,
            "lookback_days": lookback_days,
        },
    )


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #


def run_for_strategy(
    strategy: str,
    *,
    events: list[dict[str, Any]],
    backtest_summary: dict[str, Any] | None,
    other_live_strategies: list[str],
    asof: datetime | None = None,
) -> StrategyReport:
    """Run all 9 criteria and roll up to an overall verdict."""
    asof = asof or datetime.now(UTC)
    checks = [
        check_criterion_1_forward_paper_duration(strategy, events=events, asof=asof),
        check_criterion_2_live_sharpe_vs_backtest(
            strategy, events=events, backtest_summary=backtest_summary
        ),
        check_criterion_3_live_dd_vs_backtest(
            strategy, events=events, backtest_summary=backtest_summary
        ),
        check_criterion_4_total_trades(strategy, events=events),
        check_criterion_5_slippage_mae(strategy, events=events),
        check_criterion_6_risk_cap_breaches(strategy, events=events, asof=asof),
        check_criterion_7_coherence(
            strategy, events=events, backtest_summary=backtest_summary, asof=asof
        ),
        check_criterion_8_drift_halts(strategy, events=events, asof=asof),
        check_criterion_9_pairwise_correlation(
            strategy,
            events=events,
            other_live_strategies=other_live_strategies,
            asof=asof,
        ),
    ]
    blocking = [c.n for c in checks if c.result == "FAIL"]
    if blocking:
        overall: OverallResult = "NOT_READY"
    elif all(c.result == "PASS" for c in checks):
        overall = "READY"
    else:
        overall = "INDETERMINATE"
    return StrategyReport(
        strategy=strategy,
        asof=asof.isoformat(),
        criteria=checks,
        overall=overall,
        blocking=blocking,
    )


def _load_backtest_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.exists():
        logger.warning("backtest-summary path %s does not exist", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("failed to parse %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("backtest-summary must be a JSON object, got %s", type(data).__name__)
        return None
    return data


def _portfolio_summary(reports: list[StrategyReport]) -> dict[str, Any]:
    """Aggregate strategy reports into a portfolio-level view."""
    n = len(reports)
    ready = sum(1 for r in reports if r.overall == "READY")
    not_ready = sum(1 for r in reports if r.overall == "NOT_READY")
    indeterminate = sum(1 for r in reports if r.overall == "INDETERMINATE")
    blocking_factors: dict[int, list[str]] = {}
    for r in reports:
        for cn in r.blocking:
            blocking_factors.setdefault(cn, []).append(r.strategy)
    return {
        "n_strategies": n,
        "ready": ready,
        "not_ready": not_ready,
        "indeterminate": indeterminate,
        "blocking_factors": {str(k): v for k, v in sorted(blocking_factors.items())},
    }


# --------------------------------------------------------------------------- #
# Output                                                                      #
# --------------------------------------------------------------------------- #


def _format_pretty(report: StrategyReport, report_path: Path | None) -> str:
    lines: list[str] = []
    lines.append(
        f"Phase 9 Live-Readiness — strategy={report.strategy} — asof {report.asof}"
    )
    for c in report.criteria:
        # Pad criterion name to a fixed column for readability.
        col_name = f"[{c.n}/9] {c.name}".ljust(38)
        col_result = c.result.ljust(15)
        lines.append(f"{col_name}{col_result}({c.detail})")
    lines.append("")
    lines.append(f"Overall: {report.overall}")
    if report.blocking:
        lines.append(f"Blocking: {report.blocking}")
    if report_path is not None:
        lines.append(f"Detailed JSON: {report_path}")
    return "\n".join(lines)


def _format_portfolio_pretty(reports: list[StrategyReport], summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Phase 9 Live-Readiness — PORTFOLIO")
    lines.append(
        f"strategies={summary['n_strategies']}  "
        f"ready={summary['ready']}  "
        f"not_ready={summary['not_ready']}  "
        f"indeterminate={summary['indeterminate']}"
    )
    for r in reports:
        lines.append(
            f"  - {r.strategy:30s} {r.overall:14s} "
            f"blocking={r.blocking if r.blocking else '[]'}"
        )
    if summary["blocking_factors"]:
        lines.append("")
        lines.append("Blocking factors:")
        for cn, strats in summary["blocking_factors"].items():
            lines.append(f"  criterion {cn}: {', '.join(strats)}")
    return "\n".join(lines)


def _write_json_report(report: StrategyReport, report_dir: Path, asof: datetime) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    fname = f"live_ready_{report.strategy}_{asof.strftime('%Y-%m-%d')}.json"
    path = report_dir / fname
    path.write_text(json.dumps(asdict(report), indent=2, default=str), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="check_live_ready.py",
        description="Phase 9 live-readiness gate (read-only audit).",
    )
    p.add_argument(
        "--strategy",
        required=True,
        help='Strategy name to audit. Use "all" to audit every strategy in the journal.',
    )
    p.add_argument(
        "--backtest-summary",
        type=Path,
        default=None,
        help="JSON file mapping strategy_name -> {sharpe, max_dd, win_rate, n_trades}.",
    )
    p.add_argument(
        "--journal-dir",
        type=Path,
        default=Path("journal"),
        help="Directory containing YYYY-MM-DD.jsonl journal files (default: ./journal).",
    )
    p.add_argument(
        "--portfolio",
        action="store_true",
        help="Also print a portfolio-level summary (implied when --strategy=all).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout instead of pretty text.",
    )
    p.add_argument(
        "--report-dir",
        type=Path,
        default=Path("live/runtime"),
        help="Where to write detailed per-strategy JSON reports.",
    )
    p.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level for the script itself.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(message)s")

    asof = datetime.now(UTC)

    journal_dir: Path = args.journal_dir
    events = read_all_journal_events(journal_dir)

    backtest_summary = _load_backtest_summary(args.backtest_summary)

    if args.strategy == "all":
        strategies = _list_strategies(events)
    else:
        strategies = [args.strategy]

    if not strategies:
        if args.json:
            sys.stdout.write(
                json.dumps(
                    {
                        "asof": asof.isoformat(),
                        "strategies": {},
                        "portfolio": {
                            "n_strategies": 0,
                            "ready": 0,
                            "not_ready": 0,
                            "indeterminate": 0,
                            "blocking_factors": {},
                        },
                    },
                    indent=2,
                )
                + "\n"
            )
        else:
            sys.stdout.write(
                f"No strategies found in journal {journal_dir}. "
                "Either the directory is empty or no events have a 'strategy' field.\n"
            )
        return 0

    other_live = list(strategies)
    reports: list[StrategyReport] = []
    for s in strategies:
        report = run_for_strategy(
            s,
            events=events,
            backtest_summary=backtest_summary,
            other_live_strategies=other_live,
            asof=asof,
        )
        reports.append(report)

    # Always write detailed JSON reports per strategy.
    report_paths: dict[str, Path] = {}
    for r in reports:
        try:
            report_paths[r.strategy] = _write_json_report(r, args.report_dir, asof)
        except OSError as exc:
            logger.warning("could not write report for %s: %s", r.strategy, exc)

    portfolio = _portfolio_summary(reports)

    if args.json:
        out = {
            "asof": asof.isoformat(),
            "strategies": {
                r.strategy: {
                    "asof": r.asof,
                    "overall": r.overall,
                    "blocking": r.blocking,
                    "criteria": [asdict(c) for c in r.criteria],
                }
                for r in reports
            },
            "portfolio": portfolio,
        }
        sys.stdout.write(json.dumps(out, indent=2, default=str) + "\n")
        return 0

    pieces: list[str] = []
    for r in reports:
        pieces.append(_format_pretty(r, report_paths.get(r.strategy)))
    if args.portfolio or args.strategy == "all":
        pieces.append("")
        pieces.append(_format_portfolio_pretty(reports, portfolio))
    sys.stdout.write("\n\n".join(pieces) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
