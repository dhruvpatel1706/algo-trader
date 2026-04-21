# Backtest results — honest write-up

This file describes what each strategy in `src/strategies/` is **expected** to look like in backtest, the **failure modes** to watch for, and how to interpret the artifacts the backtester drops here.

> **No win-rate promises.** Past metrics are facts about a closed dataset. The future is not bound by them. Every number in this document is an order-of-magnitude expectation, not a target.

## Artifacts per run

After each backtest you'll find a directory `backtests/<strategy_name>/<UTC-timestamp>/` containing:

| File | Contents |
| --- | --- |
| `metrics.json` | Sharpe, Sortino, Calmar, max drawdown, profit factor, expectancy, win rate, n_trades, period, git SHA, warnings, survivorship_check |
| `equity.png` | Equity curve with drawdown shading |
| `trades.parquet` | Per-trade ledger (symbol, entry/exit timestamps and prices, qty, pnl, strategy_tag) |

To run a backtest:

```bash
uv run python -m src.backtest.cli --strategy mr_etf --start 2018-01-02 --end 2025-04-18
```

## `mr_etf` — Bollinger + RSI(2) mean reversion, ADX-gated

**Thesis.** In low-trend (ADX<20) regimes, sharp pullbacks on liquid index ETFs that close at/below the lower Bollinger Band with RSI(2)<10 tend to revert toward the 20-period SMA within 1-5 trading days. Exit on the SMA touch; stop is 2× ATR(14) below entry.

### What to expect (illustrative; check `metrics.json` for the actual run)
- **Sharpe**: 0.4 – 0.8 on SPY/QQQ daily bars, 2018–2024.
- **Max drawdown**: 15 – 25%, concentrated in regime-shift periods (Q1 2020, Q4 2018, Aug 2024).
- **Win rate**: 55 – 65%.
- **Profit factor**: 1.1 – 1.4.
- **Trade count**: ~30 – 60 per year per symbol — too few in any single year for the win rate to be meaningfully estimated; combine years.

### Known failure modes
1. **Regime shift mid-trade.** ADX rising above 20 → mean reversion stops working. The strategy doesn't re-check ADX intra-trade; the stop is your only protection.
2. **Vol shocks (VIX>30).** ATR widens, the 2x stop is far away, the bounce never comes. Q1 2020 will dominate the drawdown column.
3. **Gap-down opens.** Signal fires on the close; next-day open gaps below the stop. Fill drifts well below the stop level. Slippage can cost more than 1 ATR.
4. **Trend persistence.** In a true persistent downtrend, "oversold" stays oversold for weeks. The ADX gate helps but isn't a guarantee.
5. **Survivorship & data quality.** Free-tier yfinance data is good enough for prototyping. For sizing decisions, use Alpaca SIP data and re-validate.

### What would invalidate the strategy
- Sharpe < 0.2 over a multi-year out-of-sample window with ≥ 100 trades.
- Profit factor < 1.0.
- Drawdown > 35% on liquid index ETFs.

## `wheel_etf` — Cash-secured put wheel (v1 stub)

**Status: emits no signals in v1.** See `src/strategies/wheel_etf.py` module docstring.

A real CSP backtest needs an options chain, IVR series, Greeks, and assignment mechanics. None of that is wired in v1. The parameter dataclass (`WheelParams`) is the design surface and is preserved so we can enable signals once the options engine lands.

When enabled, expectations to set with eyes open:
- **Most days nothing happens.** IVR>30 + 30Δ + 30-45 DTE setups are not abundant.
- **Win rate is high (~70-80%) but tail risk is real.** Premium is a small payoff against a fat tail. A single Q1-2020 wipes months of premium.
- **Capital is locked.** Cash-account CSPs require full collateral on each contract.

Don't enable signals until the backtest engine + options data + IVR computation have been validated end-to-end.

## How to read `metrics.json`

| Field | Meaning | Sanity check |
| --- | --- | --- |
| `sharpe` | (mean - rf) / std × √252 | ≥ 30 trades; otherwise it's noise |
| `sortino` | as Sharpe but only downside vol | should be ≥ Sharpe |
| `calmar` | annual return / max DD | < 1 is normal for retail systems |
| `max_dd` | peak-to-trough drawdown (positive fraction) | the only number you need to be honest about |
| `profit_factor` | sum(wins) / |sum(losses)| | > 1.5 is good; > 2 is suspicious |
| `expectancy` | avg pnl per trade ($) | sign matters more than magnitude here |
| `n_trades` | total trades in the run | < 30 → metrics are noise |
| `warnings` | engine flags | look-ahead, implausible fill, etc. — investigate every entry |
| `survivorship_check` | `explicit_index_membership` for SPY/QQQ/IWM/DIA, else `none` | `none` for individual stocks is a real risk |
