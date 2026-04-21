"""Backtest CLI: `uv run python -m src.backtest.cli --strategy <name> --start ... --end ...`.

Outputs `backtests/<strategy>/<UTC-timestamp>/` with metrics.json, equity.png, trades.parquet.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
from datetime import UTC, datetime

import matplotlib

matplotlib.use("Agg")  # headless

import matplotlib.pyplot as plt
import pandas as pd

from src.backtest.metrics import summarize
from src.backtest.walk_forward import run_walk_forward
from src.config import PROJECT_ROOT
from src.data.loader import load_daily_bars
from src.strategies import load_strategy

_INDEX_ETFS = {"SPY", "QQQ", "IWM", "DIA"}


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short=8", "HEAD"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a walk-forward backtest.")
    p.add_argument("--strategy", required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--train-bars", type=int, default=252)
    p.add_argument("--test-bars", type=int, default=63)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    strat = load_strategy(args.strategy)
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    bars = load_daily_bars(strat.universe(), start, end)
    if not bars:
        print(f"No bars loaded for {strat.universe()}.", file=sys.stderr)
        return 1

    result = run_walk_forward(strat, bars, train_bars=args.train_bars, test_bars=args.test_bars)
    metrics = summarize(result.equity, result.returns, result.trades)
    # Joined-equity is canonical (Option B). Per-window stats are the stability check.
    metrics["per_window_sharpe_mean"] = round(result.per_window_sharpe_mean, 3)
    metrics["per_window_sharpe_std"] = round(result.per_window_sharpe_std, 3)
    metrics["n_windows"] = result.n_windows
    metrics["strategy"] = strat.name
    metrics["version"] = _git_sha()
    metrics["period"] = [args.start, args.end]
    metrics["warnings"] = result.warnings
    metrics["survivorship_check"] = (
        "explicit_index_membership" if all(s in _INDEX_ETFS for s in strat.universe()) else "none"
    )

    out_dir = PROJECT_ROOT / "backtests" / strat.name / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    if result.trades:
        pd.DataFrame([dataclasses.asdict(t) for t in result.trades]).to_parquet(
            out_dir / "trades.parquet"
        )

    eq = result.equity
    peak = eq.cummax()
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(eq.index, eq.values, label="equity")
    ax.fill_between(
        eq.index,
        peak.values,
        eq.values,
        where=eq.values < peak.values,
        alpha=0.2,
        color="red",
        label="drawdown",
    )
    ax.set_title(f"{strat.name} walk-forward — {args.start} → {args.end}")
    ax.set_ylabel("equity ($)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "equity.png", dpi=120)
    plt.close(fig)

    print(json.dumps(metrics, indent=2, default=str))
    print(f"\nArtifacts: {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
