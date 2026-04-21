---
name: backtest-strategy
description: Runs a disciplined walk-forward backtest with realistic slippage and commissions. Use PROACTIVELY whenever the user mentions backtest, validate, evaluate, "test this strategy", or asks for Sharpe/drawdown numbers. Triggers extended thinking (ultrathink) for parameter-sensitivity reasoning.
---

# Backtest a strategy (walk-forward + realistic costs)

Use **ultrathink** for parameter sensitivity and failure-mode reasoning.

## Steps

1. **Load strategy.** `from src.strategies import load_strategy; strat = load_strategy("<name>")` (e.g. `mr_etf`).
2. **Load bars.** Daily OHLCV from `src/data/loader.py` for `strat.universe()`.
3. **Walk-forward.** 252-bar train, 63-bar test, no overlap on test. Use `src/backtest/walk_forward.py`.
4. **Costs.** ATR-proportional slippage = `0.05 * ATR14` per fill. Commission `$0.005/share` equities, `$0.65/contract` options.
5. **Run.** `uv run python -m src.backtest.run --strategy <name> --start 2018-01-02 --end 2025-04-18`.
6. **Emit.** `backtests/<strategy>/<UTC-timestamp>/metrics.json`, `equity.png`, `trades.parquet`.
7. **Summarize.** Print Sharpe, Sortino, Calmar, profit factor, max DD, expectancy, trade count, % time in market, plus any sanity-check warnings.

## Output convention

Never report a Sharpe without:
- a maximum drawdown,
- a trade count (≥ 30 to be remotely meaningful),
- the date range backtested,
- any flagged warnings (look-ahead, survivorship, implausible fills).
