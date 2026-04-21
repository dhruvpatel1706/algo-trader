"""mr_etf parameter-sensitivity sweep.

Dimensions:
  - timeframes:   [daily, 4h, 1h]   (4h and 1h emit as BLOCKED until intraday loader lands)
  - ADX threshold: [15, 20, 25, 30]
  - RSI threshold: [5, 10, 15]
  - universe:     [SPY/QQQ, top 20 liquid ETFs, top 50 large-caps]
                  (definitions in docs/universes.yaml)

For each cell: n_trades, sharpe, max_dd, profit_factor (+ sortino, expectancy, win_rate).

Outputs:
  backtests/mr_etf_sweep/<UTC-ts>/results.parquet
  backtests/mr_etf_sweep/<UTC-ts>/summary.md

Flags the 5 best cells with n_trades >= 100 AND sharpe >= 1.0 AND max_dd <= 0.20.
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.backtest.sweep import cells_to_dataframe, run_sweep  # noqa: E402
from src.config import PROJECT_ROOT  # noqa: E402
from src.strategies.mr_etf import MrEtf, MrParams  # noqa: E402

PARAM_GRID = {
    "adx_max": [15.0, 20.0, 25.0, 30.0],
    "rsi_oversold": [5.0, 10.0, 15.0],
}
TIMEFRAMES = ("1d", "4h", "1h")
START = date(2022, 1, 1)
END = date(2024, 12, 31)

ELIGIBILITY_FILTER = "n_trades >= 100 and sharpe >= 1.0 and max_dd <= 0.20"


def _load_universes() -> dict[str, tuple[str, ...]]:
    path = PROJECT_ROOT / "docs" / "universes.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {name: tuple(syms) for name, syms in raw.items()}


def _factory(combo: dict) -> MrEtf:
    """Build a MrEtf with overridden ADX + RSI thresholds; other params at defaults."""
    base = MrParams()
    return MrEtf(dataclasses.replace(base, **combo))


def _markdown_table(df, columns: list[str]) -> str:
    """Render a pandas DataFrame to a GitHub-flavored markdown table without `tabulate`."""
    if df.empty:
        return "_(no rows)_"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in columns:
            v = r[c]
            if isinstance(v, float):
                cells.append(f"{v:.4f}" if abs(v) < 1000 else f"{v:.2f}")
            else:
                cells.append(str(v))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *rows])


def _summarize_markdown(df, out_dir: Path, eligible) -> str:
    n_total = len(df)
    n_skipped = int(df["skipped"].sum())
    n_real = n_total - n_skipped

    lines = []
    lines.append(f"# mr_etf sensitivity sweep — {datetime.now(UTC).isoformat()}")
    lines.append("")
    lines.append(f"- **Period:** {START} → {END}")
    lines.append(f"- **Cells total:** {n_total}")
    lines.append(f"- **Cells with metrics:** {n_real}")
    lines.append(f"- **Cells skipped:** {n_skipped}")
    lines.append("")
    lines.append("## Top cells meeting `n_trades >= 100 AND sharpe >= 1.0 AND max_dd <= 0.20`")
    lines.append("")
    if eligible.empty:
        lines.append("**None.** No configuration crossed all three thresholds.")
        lines.append("")
        lines.append("Which threshold bound matters — see the full table for closest misses.")
    else:
        lines.append(
            _markdown_table(
                eligible,
                [
                    "universe",
                    "timeframe",
                    "adx_max",
                    "rsi_oversold",
                    "n_trades",
                    "sharpe",
                    "per_window_sharpe_mean",
                    "per_window_sharpe_std",
                    "max_dd",
                    "profit_factor",
                ],
            )
        )
        lines.append("")
        lines.append(
            "_`sharpe` is computed on the joined / compounded equity (Option B). "
            "`per_window_sharpe_mean` and `_std` are stability stats from the "
            "Option A per-window standalone Sharpes. Big std => the edge is "
            "regime-dependent._"
        )
    lines.append("")
    lines.append("## Skipped cells (by reason)")
    lines.append("")
    skipped_groups = (
        df[df["skipped"]]
        .groupby("skip_reason")
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    if skipped_groups.empty:
        lines.append("_(none)_")
    else:
        lines.append(_markdown_table(skipped_groups, ["skip_reason", "count"]))
    lines.append("")
    lines.append("## Full results")
    lines.append("")
    lines.append(
        _markdown_table(
            df.sort_values(["universe", "timeframe", "adx_max", "rsi_oversold"]),
            [
                "universe",
                "timeframe",
                "adx_max",
                "rsi_oversold",
                "n_trades",
                "sharpe",
                "per_window_sharpe_mean",
                "per_window_sharpe_std",
                "n_windows",
                "max_dd",
                "profit_factor",
                "skipped",
                "skip_reason",
            ],
        )
    )
    return "\n".join(lines)


def main() -> int:
    universes = _load_universes()
    print(f"Universes: {list(universes.keys())}", file=sys.stderr)
    print(f"Timeframes: {TIMEFRAMES}", file=sys.stderr)
    print(f"Param grid: {PARAM_GRID}", file=sys.stderr)
    n_total = (
        len(universes)
        * len(TIMEFRAMES)
        * len(PARAM_GRID["adx_max"])
        * len(PARAM_GRID["rsi_oversold"])
    )
    print(
        f"Total cells = {len(universes)} x {len(TIMEFRAMES)} x "
        f"{len(PARAM_GRID['adx_max'])} x {len(PARAM_GRID['rsi_oversold'])} = {n_total}",
        file=sys.stderr,
    )

    cells = run_sweep(
        _factory,
        universes,
        PARAM_GRID,
        timeframes=TIMEFRAMES,
        start=START,
        end=END,
    )
    df = cells_to_dataframe(cells)

    out_dir = (
        PROJECT_ROOT / "backtests" / "mr_etf_sweep" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "results.parquet", index=False)

    real = df[~df["skipped"].astype(bool)].copy()
    eligible = real.query(ELIGIBILITY_FILTER).sort_values("sharpe", ascending=False).head(5)

    (out_dir / "summary.md").write_text(_summarize_markdown(df, out_dir, eligible))

    print(f"Wrote {len(df)} rows to {out_dir}/results.parquet")
    print(f"Wrote markdown summary to {out_dir}/summary.md")
    print(f"Eligible cells (n_trades>=100 AND sharpe>=1.0 AND max_dd<=0.20): {len(eligible)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
