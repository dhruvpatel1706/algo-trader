"""Multi-strategy backtest wrapper around `BacktestEngine`.

Why this exists
---------------
`BacktestEngine` (in `engine.py`) runs ONE strategy through a synthetic equity
account. Real portfolios run several strategies concurrently against a SHARED
pool of capital, and we care about cross-strategy effects: combined drawdown,
how each strategy contributes to total return, and whether the strategies are
diversified or unintentionally trading the same theme.

This module composes N independent `BacktestEngine` instances — one per
strategy — over the same calendar of bars, aggregates their per-bar equity
into a single joined equity series, and reports per-strategy contributions
and a pairwise return-correlation matrix.

Design choice
-------------
We deliberately do NOT modify `BacktestEngine`. Each strategy gets its own
engine instance with `starting_equity / N` as a per-strategy seed; the joined
equity series at any timestamp is the SUM of the per-strategy equity values.
This preserves a "shared pool" invariant at t0 (sum == starting_equity) and
keeps each strategy's per-bar position-sizing math byte-for-byte identical
to a standalone backtest of the same strategy at that seed.

Position bookkeeping is keyed by `(strategy_tag, symbol)` — the same symbol
can be held by two different strategies at once, because they live in
separate engines and each engine enforces "one position per symbol".

If `src.risk.correlation.correlation_penalty` is available, it is exposed
for callers that want to penalize correlated entries; the multi-engine itself
does not invoke it (the underlying `BacktestEngine` performs sizing), but
keeping the import here documents the integration point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
import pandas as pd

from src.backtest.costs import DEFAULT_COSTS, CostModel
from src.backtest.engine import BacktestEngine, BacktestResult
from src.backtest.metrics import TradeRecord
from src.strategies.base import Strategy

try:  # pragma: no cover - import guard, exercised only when correlation.py is missing
    from src.risk.correlation import correlation_penalty
except ImportError:  # pragma: no cover

    def correlation_penalty(*_args, **_kwargs) -> float:  # type: ignore[misc]
        return 1.0


@dataclass
class MultiBacktestResult:
    """Aggregated result of running N strategies through `MultiStrategyEngine.run()`.

    Attributes
    ----------
    per_strategy:
        Map of strategy name -> the underlying `BacktestResult` produced by
        that strategy's `BacktestEngine`. Every metric exposed by
        `BacktestResult` (equity, returns, trades, warnings, walk-forward
        stability) is preserved here untouched.
    equity:
        Joined equity series. At each timestamp, equal to the SUM of the
        per-strategy equity values at the same timestamp. This is the line
        a single trader running all strategies would have experienced.
    returns:
        Per-bar pct-change of `equity`.
    trades:
        All trades from all strategies, sorted by exit timestamp. Each trade
        retains its `strategy_tag`, so callers can group/filter freely.
    contributions:
        Map of strategy name -> contribution to total joined return, where
        `contribution = (strategy_end_equity - strategy_start_equity) /
        joined_starting_equity`. Sum of contributions == joined total return.
    correlation_matrix:
        Pairwise correlation of per-strategy daily returns. Shape (N, N), unit
        diagonal. Empty DataFrame if fewer than two strategies have at least
        two observations.
    starting_equity:
        The user-supplied starting equity (echoed for downstream reporting).
    """

    per_strategy: dict[str, BacktestResult]
    equity: pd.Series
    returns: pd.Series
    trades: list[TradeRecord]
    contributions: dict[str, float]
    correlation_matrix: pd.DataFrame
    starting_equity: float
    warnings: list[str] = field(default_factory=list)


class MultiStrategyEngine:
    """Run N strategies through `BacktestEngine` instances over a shared pool.

    The starting equity is split equally across strategies; each engine then
    runs independently. Aggregation happens after all engines complete.
    """

    def __init__(
        self,
        strategies: list[Strategy],
        starting_equity: Decimal = Decimal("100000"),
        costs: CostModel = DEFAULT_COSTS,
    ) -> None:
        if not strategies:
            raise ValueError("MultiStrategyEngine requires at least one strategy")
        self.strategies = list(strategies)
        self.starting_equity = Decimal(str(starting_equity))
        self.costs = costs
        # Resolve display names eagerly so duplicate-tag misuse is loud.
        self._strategy_names: list[str] = []
        for s in self.strategies:
            tag = getattr(s, "name", None) or s.__class__.__name__
            if tag in self._strategy_names:
                raise ValueError(f"duplicate strategy name in MultiStrategyEngine: {tag}")
            self._strategy_names.append(tag)

    def run(
        self,
        bars: dict[str, pd.DataFrame],
    ) -> MultiBacktestResult:
        """Run each strategy through its own `BacktestEngine` and aggregate."""
        if not bars:
            raise ValueError("no bars to backtest")

        n = len(self.strategies)
        per_strategy_seed = self.starting_equity / Decimal(n)

        per_strategy: dict[str, BacktestResult] = {}
        equity_frames: list[pd.Series] = []
        all_trades: list[TradeRecord] = []
        all_warnings: list[str] = []

        for name, strat in zip(self._strategy_names, self.strategies, strict=True):
            engine = BacktestEngine(
                strategy=strat,
                starting_equity=per_strategy_seed,
                costs=self.costs,
            )
            # Restrict bars to the strategy's declared universe if it has one;
            # otherwise pass everything. This keeps each engine focused and
            # avoids the engine warning on signals it cannot place.
            try:
                universe = set(strat.universe())
            except (NotImplementedError, AttributeError):
                universe = set(bars.keys())
            sub_bars = {sym: df for sym, df in bars.items() if sym in universe} or bars

            result = engine.run(sub_bars)
            # Re-tag trades that came in without a strategy_tag so per-strategy
            # isolation is reliable downstream.
            tagged_trades: list[TradeRecord] = []
            for t in result.trades:
                if t.strategy_tag == name:
                    tagged_trades.append(t)
                    continue
                tagged_trades.append(
                    TradeRecord(
                        symbol=t.symbol,
                        side=t.side,
                        entry_ts=t.entry_ts,
                        exit_ts=t.exit_ts,
                        qty=t.qty,
                        entry_price=t.entry_price,
                        exit_price=t.exit_price,
                        pnl=t.pnl,
                        strategy_tag=name,
                    )
                )

            tagged_result = BacktestResult(
                equity=result.equity,
                returns=result.returns,
                trades=tagged_trades,
                warnings=result.warnings,
                per_window_sharpe_mean=result.per_window_sharpe_mean,
                per_window_sharpe_std=result.per_window_sharpe_std,
                n_windows=result.n_windows,
            )
            per_strategy[name] = tagged_result
            equity_frames.append(tagged_result.equity.rename(name))
            all_trades.extend(tagged_result.trades)
            all_warnings.extend(f"[{name}] {w}" for w in tagged_result.warnings)

        # Joined equity = sum per-strategy at each timestamp.
        equity_df = pd.concat(equity_frames, axis=1).sort_index()
        # Forward-fill so a strategy that has no bar at some timestamp keeps
        # its last known equity rather than NaN-ing out the whole row.
        equity_df = equity_df.ffill()
        # Backfill the head for strategies whose first bar lands later.
        equity_df = equity_df.bfill()
        joined_equity = equity_df.sum(axis=1).rename("equity")
        joined_returns = joined_equity.pct_change().fillna(0.0).rename("returns")

        # Contributions: each strategy's $ change as a fraction of total seed.
        contributions: dict[str, float] = {}
        seed_total = float(self.starting_equity)
        for name, res in per_strategy.items():
            if res.equity.empty:
                contributions[name] = 0.0
                continue
            delta = float(res.equity.iloc[-1]) - float(res.equity.iloc[0])
            contributions[name] = delta / seed_total if seed_total > 0 else 0.0

        # Pairwise correlation of per-strategy DAILY returns (the per-strategy
        # equity series is already aligned in equity_df).
        if equity_df.shape[1] >= 2 and len(equity_df) >= 2:
            ret_df = equity_df.pct_change().fillna(0.0)
            correlation_matrix = ret_df.corr()
            # Replace NaN (e.g. constant series) with 0.0 off-diagonal, 1.0 diag.
            correlation_matrix = correlation_matrix.fillna(0.0)
            np.fill_diagonal(correlation_matrix.values, 1.0)
        else:
            correlation_matrix = pd.DataFrame()

        # Trades sorted by exit_ts for a stable joined timeline.
        all_trades.sort(key=lambda t: t.exit_ts)

        return MultiBacktestResult(
            per_strategy=per_strategy,
            equity=joined_equity,
            returns=joined_returns,
            trades=all_trades,
            contributions=contributions,
            correlation_matrix=correlation_matrix,
            starting_equity=seed_total,
            warnings=all_warnings,
        )
