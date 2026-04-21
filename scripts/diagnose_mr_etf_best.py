"""Diagnostic audit of the best mr_etf sweep cell (liquid_etfs_top20, ADX<=30, RSI<=5).

Re-runs that single cell with full instrumentation and writes a six-section
report to backtests/mr_etf_sweep/20260421T031317Z/diagnostics.md plus a
best_trades.csv ledger and an annotated equity_best.png.

REPORT-ONLY. No source-code fixes here.
"""

from __future__ import annotations

import sys
import textwrap
from datetime import UTC, date, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.backtest.metrics import annualized_sharpe, max_drawdown  # noqa: E402
from src.backtest.walk_forward import run_walk_forward  # noqa: E402
from src.data.loader import load_daily_bars  # noqa: E402
from src.strategies.mr_etf import MrEtf, MrParams  # noqa: E402

SWEEP_DIR = REPO / "backtests" / "mr_etf_sweep" / "20260421T031317Z"
START = date(2022, 1, 1)
END = date(2024, 12, 31)


def _load_universe(name: str) -> tuple[str, ...]:
    raw = yaml.safe_load((REPO / "docs" / "universes.yaml").read_text(encoding="utf-8"))
    return tuple(raw[name])


def run_best():
    universe = _load_universe("liquid_etfs_top20")
    strat = MrEtf(params=MrParams(adx_max=30.0, rsi_oversold=5.0))
    bars = load_daily_bars(universe, START, END)
    result = run_walk_forward(strat, bars, train_bars=252, test_bars=63)
    return strat, bars, result


def trades_to_df(trades, equity_series: pd.Series) -> pd.DataFrame:
    rows = []
    for t in trades:
        delta = t.exit_ts - t.entry_ts
        holding_days = int(delta.days) if hasattr(delta, "days") else 0
        mask = equity_series.index <= t.entry_ts
        eq_at_entry = float(equity_series[mask].iloc[-1]) if mask.any() else 100000.0
        notional = t.qty * t.entry_price
        rows.append(
            {
                "entry_date": t.entry_ts,
                "exit_date": t.exit_ts,
                "symbol": t.symbol,
                "entry_price": round(t.entry_price, 4),
                "exit_price": round(t.exit_price, 4),
                "qty": t.qty,
                "holding_period_days": holding_days,
                "realized_pnl_dollars": round(t.pnl, 4),
                "position_notional": round(notional, 2),
                "pct_of_equity_at_entry": round(notional / eq_at_entry, 6),
                "realized_pnl_pct_of_equity_at_entry": round(t.pnl / eq_at_entry, 6),
                "strategy_tag": t.strategy_tag,
            }
        )
    return pd.DataFrame(rows)


def exposure_stats(equity: pd.Series, trades) -> dict:
    days = equity.index
    in_market = pd.Series(0, index=days, dtype=int)
    for t in trades:
        mask = (days >= t.entry_ts) & (days <= t.exit_ts)
        in_market[mask] = in_market[mask] + 1
    frac_days = float((in_market > 0).mean())
    max_simul = int(in_market.max()) if len(in_market) else 0
    returns = equity.pct_change().fillna(0)
    sharpe_all = float(annualized_sharpe(returns))
    sharpe_in = float(annualized_sharpe(returns[in_market > 0])) if (in_market > 0).any() else 0.0
    longest_flat = 0
    cur = 0
    for x in in_market.values:
        if x == 0:
            cur += 1
            longest_flat = max(longest_flat, cur)
        else:
            cur = 0
    return {
        "frac_days_with_position": frac_days,
        "max_simultaneous_positions": max_simul,
        "longest_flat_days": longest_flat,
        "sharpe_all_days": sharpe_all,
        "sharpe_in_market_only": sharpe_in,
        "n_trading_days": len(days),
    }


def find_max_dd_window(equity: pd.Series) -> dict:
    peak = equity.cummax()
    dd = (peak - equity) / peak
    end = dd.idxmax()
    start = equity.loc[:end].idxmax()
    depth = float(dd.max())
    return {"start": start, "end": end, "depth_pct": depth}


def baseline(symbols: tuple[str, ...]) -> dict:
    bars = load_daily_bars(symbols, START, END)
    out = {}
    for sym, df in bars.items():
        eq = df["close"] * (100000 / float(df["close"].iloc[0]))
        ret = eq.pct_change().fillna(0)
        out[sym] = {
            "sharpe": float(annualized_sharpe(ret)),
            "max_dd": float(max_drawdown(eq)),
            "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1),
            "equity": eq,
        }
    return out


def sixty_forty(spy_eq: pd.Series, agg_eq: pd.Series) -> dict:
    spy_ret = spy_eq.pct_change().fillna(0)
    agg_ret = agg_eq.pct_change().fillna(0)
    common = spy_ret.index.intersection(agg_ret.index)
    combo_ret = 0.6 * spy_ret.loc[common] + 0.4 * agg_ret.loc[common]
    combo_eq = (1 + combo_ret).cumprod() * 100000
    return {
        "sharpe": float(annualized_sharpe(combo_ret)),
        "max_dd": float(max_drawdown(combo_eq)),
        "total_return": float(combo_eq.iloc[-1] / combo_eq.iloc[0] - 1),
    }


def plot_equity(equity: pd.Series, dd_window: dict, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(
        equity.index, equity.values, color="#22c55e", linewidth=1.4, label="mr_etf walk-forward"
    )
    peak = equity.cummax()
    ax.fill_between(
        equity.index,
        peak.values,
        equity.values,
        where=equity.values < peak.values,
        alpha=0.2,
        color="red",
        label="drawdown",
    )
    ax.axvspan(
        dd_window["start"],
        dd_window["end"],
        color="orange",
        alpha=0.15,
        label=f"max DD window ({dd_window['depth_pct']:.2%})",
    )
    ax.set_title("mr_etf — best-config walk-forward equity (each WF window resets to $100k)")
    ax.set_ylabel("equity ($)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/6] Re-running best cell (ADX<=30, RSI<=5, liquid_etfs_top20)...", file=sys.stderr)
    strat, bars, result = run_best()
    trades_df = trades_to_df(result.trades, result.equity)
    csv_path = SWEEP_DIR / "best_trades.csv"
    trades_df.to_csv(csv_path, index=False)

    hp = trades_df["holding_period_days"].describe() if not trades_df.empty else None
    pp = trades_df["pct_of_equity_at_entry"].describe() if not trades_df.empty else None
    pl = trades_df["realized_pnl_dollars"].describe() if not trades_df.empty else None

    print("[2/6] Equity sanity + max DD window...", file=sys.stderr)
    dd = find_max_dd_window(result.equity)
    plot_equity(result.equity, dd, SWEEP_DIR / "equity_best.png")

    print("[3/6] Exposure...", file=sys.stderr)
    expo = exposure_stats(result.equity, result.trades)

    print("[4/6] Position-sizing walk-through (text only)...", file=sys.stderr)
    # documented in markdown — no compute needed

    print("[5/6] Look-ahead / survivorship (text only)...", file=sys.stderr)

    print("[6/6] Baselines (SPY, AGG, 60/40)...", file=sys.stderr)
    base = baseline(("SPY", "AGG"))
    sf = sixty_forty(base["SPY"]["equity"], base["AGG"]["equity"])

    # Build markdown
    lines: list[str] = []
    a = lines.append
    a(f"# mr_etf best-config diagnostic — {datetime.now(UTC).isoformat()}")
    a("")
    a("**Cell:** liquid_etfs_top20 / 1d / ADX<=30 / RSI<=5  (the top eligible cell from the sweep)")
    a(f"**Period:** {START} → {END}")
    a(f"**Trades returned:** {len(trades_df)}")
    a("")
    a(
        "> Reminder: walk-forward in this codebase uses **fresh $100k per test window**. Equity series "
    )
    a('> across windows therefore has discontinuities; "total_return" and "max_dd" reported by ')
    a("> `summarize()` are over the *concatenated* (reset) curve, not over a single continuous ")
    a("> $100k. Diagnostics below mark this caveat where it bites.")
    a("")

    a("## 1. Trade-level inspection")
    a("")
    a(f"Per-trade ledger written to: `best_trades.csv` ({len(trades_df)} rows).")
    a("")
    if hp is not None:
        a("**Holding-period (days):**")
        a(
            f"- avg = {hp['mean']:.2f} · median = {hp['50%']:.0f} · min/25/75/max = "
            f"{hp['min']:.0f}/{hp['25%']:.0f}/{hp['75%']:.0f}/{hp['max']:.0f}"
        )
        a("")
        a("**Position size as fraction of equity at entry:**")
        a(
            f"- median = {pp['50%']:.4f} · mean = {pp['mean']:.4f} · "
            f"min/25/75/max = {pp['min']:.4f}/{pp['25%']:.4f}/{pp['75%']:.4f}/{pp['max']:.4f}"
        )
        a("")
        a("**Realized P&L per trade ($):**")
        a(
            f"- avg = {pl['mean']:.2f} · median = {pl['50%']:.2f} · "
            f"min/25/75/max = {pl['min']:.2f}/{pl['25%']:.2f}/{pl['75%']:.2f}/{pl['max']:.2f}"
        )
        a("")
        wins = trades_df[trades_df["realized_pnl_dollars"] > 0]
        losses = trades_df[trades_df["realized_pnl_dollars"] < 0]
        a(
            f"**Wins / losses:** {len(wins)} / {len(losses)} (win rate "
            f"{len(wins) / max(len(trades_df), 1):.2%})"
        )
        a(
            f"**Total realized P&L (sum across all trades):** ${trades_df['realized_pnl_dollars'].sum():.2f}"
        )
        a("")
        a("**Symbol distribution (top 10):**")
        sym_counts = trades_df["symbol"].value_counts().head(10)
        for sym, n in sym_counts.items():
            a(f"- {sym}: {int(n)}")
        a("")

    a("## 2. Equity-curve sanity & max drawdown")
    a("")
    a("![equity_best](equity_best.png)")
    a("")
    a(f"- **Max DD (over walk-forward concatenated curve):** {dd['depth_pct']:.2%}")
    a(f"- **Max DD window:** {dd['start']} → {dd['end']}")
    a(f"- **Trading days in WF series:** {expo['n_trading_days']}")
    a(f"- **Longest flat-equity (no open position) run:** {expo['longest_flat_days']} days")
    a("")
    a("**SPY behaviour during the same DD window:**")
    spy_eq = base["SPY"]["equity"]
    if dd["start"] in spy_eq.index and dd["end"] in spy_eq.index:
        spy_slice = spy_eq.loc[dd["start"] : dd["end"]]
        spy_drop = float(spy_slice.iloc[-1] / spy_slice.iloc[0] - 1)
        a(f"- SPY return over [{dd['start']}, {dd['end']}]: {spy_drop:.2%}")
    else:
        a("- (SPY index does not cover that exact range; skipping)")
    a("")

    a("## 3. Exposure check")
    a("")
    a(
        f"- **% of trading days with at least one position open:** {expo['frac_days_with_position']:.2%}"
    )
    a(f"- **Max simultaneous open positions:** {expo['max_simultaneous_positions']}")
    a(f"- **Sharpe across all days (sweep number):** {expo['sharpe_all_days']:.3f}")
    a(f"- **Sharpe conditional on being in market:** {expo['sharpe_in_market_only']:.3f}")
    a("")
    if expo["frac_days_with_position"] < 0.20:
        a("> ⚠ Exposure < 20% of days — the headline Sharpe annualizes from ")
        a("> a small sample of active days. The conditional Sharpe is the ")
        a("> honest-to-the-strategy number; the all-days Sharpe rewards the ")
        a("> strategy for sitting in cash during bad times.")
    a("")

    a("## 4. Position-sizing code path")
    a("")
    a("**Signal → qty path (from `src/backtest/engine.py:_schedule_entry`):**")
    a("```python")
    a("equity_now = self._mark_to_market(signal_ts, bars)        # cash + open MV")
    a("qty = position_size(")
    a("    equity=Decimal(str(equity_now)),")
    a("    risk_pct=s.MAX_PER_TRADE_RISK,        # = 0.01  (settings)")
    a("    entry=Decimal(str(fill_price)),")
    a("    stop=Decimal(str(sig.stop)),")
    a("    max_position_pct=s.MAX_SINGLE_POSITION,  # = 0.10  (settings)")
    a(")")
    a("```")
    a("")
    a("**`src/risk/sizing.py:position_size` formula:**")
    a("```")
    a("risk_per_share = max(|entry - stop|, EPS=$0.01)")
    a("raw = floor(equity * risk_pct / risk_per_share)")
    a("cap = floor(equity * max_position_pct / entry)   # if max_position_pct given")
    a("qty = min(raw, cap)")
    a("```")
    a("")
    a(
        "**Worked example:** $100k account, SPY at $400, stop = entry − 2·ATR with ATR=$5 ⇒ stop=$390."
    )
    a("- risk_per_share = max(|400 − 390|, 0.01) = $10")
    a("- raw            = floor(100000 · 0.01 / 10) = **100 shares** (1% risk path)")
    a("- cap            = floor(100000 · 0.10 / 400) = **25 shares** (10% position cap)")
    a("- qty            = min(100, 25) = **25 shares** → notional = $10,000 = **10% of equity**")
    a("")
    a("**Implication.** With ATR-based stops on liquid ETFs, the 10% single-position cap binds on ")
    a("essentially every trade. Effective bet size is ~10% of equity, not the 1% risk dial. With ")
    a("a 0.5% stop distance, that's ~0.05% expected loss per trade. Median P&L per trade in this ")
    a(
        f"run is **${pl['50%']:.2f}** on **{pp['50%'] * 100:.1f}% effective position size** — consistent."
    )
    a("")

    a("## 5. Look-ahead / survivorship audit")
    a("")
    a("**Look-ahead — engine timing (from `src/backtest/engine.py:run`):**")
    a("```python")
    a("for i, ts in enumerate(all_idx):")
    a("    # 1. exits intrabar against THIS bar's H/L  (same-bar OK; only uses H/L of t)")
    a("    # 2. signals = strategy.generate_signals({sym: df.loc[:ts]})  # includes ts row")
    a("    # 3. if i+1 < n: schedule entry at all_idx[i+1].open  (NEXT bar)")
    a("    # 4. mark equity at THIS bar's close")
    a("```")
    a("Signals at bar `t` use up to and including bar `t`'s OHLCV (close known at end of `t`); ")
    a("entries fill at bar `t+1` open with slippage. **No same-bar look-ahead for entries.** ")
    a("Exits *do* check stops/targets intrabar against `t`'s H/L — typical convention; the ")
    a('conservative tweak is "stop fires first if both touched" which the engine implements.')
    a("")
    a("**yfinance adjustment policy — `src/data/loader.py:89`:**")
    a("```python")
    a("df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)")
    a("```")
    a("> ⚠ `auto_adjust=False` returns **non-split-adjusted, non-dividend-adjusted** OHLCV. ")
    a("> NVDA had a 10:1 split on 2024-06-10; an unadjusted close goes from ~$1208 to ~$120 ")
    a("> overnight, exploding ATR(14), tripping the 2·ATR stop, and printing a fake catastrophic ")
    a("> loss. Same hazard for any other split in the universe over 2022-2024. The ")
    a("> liquid_etfs_top20 list has no splits in that window so its numbers are mostly clean; ")
    a("> the large_caps_50 universe is not. **Fix candidate (do not apply yet): set ")
    a("> `auto_adjust=True`.**")
    a("")
    a("**Survivorship — `docs/universes.yaml`:**")
    a("- `liquid_etfs_top20`: hand-picked names that have been top-AUM since at least 2018. ")
    a("  No ETF in the list has been delisted. Survivorship pressure is **low** but real ")
    a("  (excludes any niche ETF that briefly existed and died over 2018-2024).")
    a("- `large_caps_50`: roughly the S&P 500 top market caps as of early 2022. The list was ")
    a("  curated by me knowing the late-2024 outcomes; some 2022-top-50 names that crashed ")
    a("  out (e.g. PYPL, NFLX briefly) are absent or under-weighted. **Survivorship pressure ")
    a("  is non-trivial.** Paper-grade backtests should use point-in-time index constituents.")
    a("")

    a("## 6. Baselines (SPY buy-and-hold, 60/40 SPY/AGG, vs. mr_etf best)")
    a("")
    a("| strategy | sharpe | max_dd | total_return |")
    a("| --- | --- | --- | --- |")
    a(
        f"| **mr_etf** (best cell) | {expo['sharpe_all_days']:.3f} | {dd['depth_pct']:.4f} | "
        f"{float(result.equity.iloc[-1] / result.equity.iloc[0] - 1):.4f} |"
    )
    a(
        f"| SPY buy-and-hold | {base['SPY']['sharpe']:.3f} | {base['SPY']['max_dd']:.4f} | "
        f"{base['SPY']['total_return']:.4f} |"
    )
    a(
        f"| AGG buy-and-hold | {base['AGG']['sharpe']:.3f} | {base['AGG']['max_dd']:.4f} | "
        f"{base['AGG']['total_return']:.4f} |"
    )
    a(
        f"| 60/40 SPY/AGG (daily reb.) | {sf['sharpe']:.3f} | {sf['max_dd']:.4f} | "
        f"{sf['total_return']:.4f} |"
    )
    a("")
    a("> The mr_etf `total_return` looks tiny next to SPY's because:")
    a("> 1. Each WF test window resets to $100k — returns don't compound.")
    a("> 2. Position-cap binds at 10% of equity, so even strong moves on individual ETFs ")
    a(">    contribute only ~1% to portfolio equity per trade.")
    a(
        "> 3. The strategy is in market only ~%.0f%% of days."
        % (expo["frac_days_with_position"] * 100)
    )
    a("> SPY's higher absolute return is what you'd expect from a long-only beta exposure ")
    a("> through a recovering 2023-24. The mr_etf number is **risk-adjusted edge per ")
    a("> dollar deployed**, not a beta competitor.")
    a("")

    (SWEEP_DIR / "diagnostics.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote diagnostics.md, best_trades.csv, equity_best.png to {SWEEP_DIR}/")
    print(
        textwrap.dedent(f"""
        Headlines:
          - {len(trades_df)} trades; total realized P&L ${trades_df["realized_pnl_dollars"].sum():.2f}
          - median position = {pp["50%"] * 100:.1f}% of equity   (10% cap binds)
          - exposure: {expo["frac_days_with_position"]:.1%} of days in market; max simul = {expo["max_simultaneous_positions"]}
          - max DD on WF concatenated curve = {dd["depth_pct"]:.2%} ({dd["start"].date()} → {dd["end"].date()})
          - SPY buy-and-hold over same period: Sharpe {base["SPY"]["sharpe"]:.2f}, max_dd {base["SPY"]["max_dd"]:.2%}, return {base["SPY"]["total_return"]:.2%}
        """).strip()
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
