"""Long-only event-driven backtest engine. One bar at a time, no look-ahead."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd

from src.backtest.costs import DEFAULT_COSTS, CostModel
from src.backtest.metrics import TradeRecord
from src.config import get_settings
from src.risk.sizing import position_size
from src.signals.indicators import atr as compute_atr
from src.strategies.base import Signal, Strategy


@dataclass
class _OpenPosition:
    symbol: str
    qty: int
    entry_ts: pd.Timestamp
    entry_price: float
    stop: float
    target: float | None
    strategy_tag: str


@dataclass
class BacktestResult:
    equity: pd.Series
    returns: pd.Series
    trades: list[TradeRecord]
    warnings: list[str] = field(default_factory=list)
    # Per-window stability metrics (only populated by walk-forward; 0/0 from a
    # single-engine run since there's only one window).
    per_window_sharpe_mean: float = 0.0
    per_window_sharpe_std: float = 0.0
    n_windows: int = 1


class BacktestEngine:
    """Long-only single-position-per-symbol event-driven loop.

    Trade flow:
      - Signals generated using bars up to (and including) bar `t`.
      - Entries fill at bar `t+1`'s open with ATR-proportional slippage.
      - Stops/targets are checked intrabar against H/L.
      - Round-trip commissions are applied from `costs`.

    What this engine does NOT do (by design — those gates fire live, not in backtest):
      - Run the full risk-manager (portfolio heat, daily-loss halt, drawdown halt).
      - Run the compliance-checker (PDT, hours, restricted list).
      - Short selling (deferred).
    """

    def __init__(
        self,
        strategy: Strategy,
        starting_equity: Decimal = Decimal("100000"),
        costs: CostModel = DEFAULT_COSTS,
    ) -> None:
        self.strategy = strategy
        self.cash = float(starting_equity)
        self.starting_equity = float(starting_equity)
        self.costs = costs
        self._open: dict[str, _OpenPosition] = {}
        self._equity_curve: list[tuple[pd.Timestamp, float]] = []
        self._trades: list[TradeRecord] = []
        self._warnings: list[str] = []

    def run(self, bars: dict[str, pd.DataFrame]) -> BacktestResult:
        all_idx = sorted({ts for df in bars.values() for ts in df.index})
        if not all_idx:
            raise ValueError("no bars to backtest")

        atrs = {sym: compute_atr(df["high"], df["low"], df["close"]) for sym, df in bars.items()}

        for i, ts in enumerate(all_idx):
            # 1. Process exits intrabar against THIS bar's H/L.
            for sym, pos in list(self._open.items()):
                df = bars[sym]
                if ts not in df.index:
                    continue
                bar = df.loc[ts]
                atr_now = float(atrs[sym].loc[ts] or 0.0)
                self._maybe_exit(pos, ts, bar, atr_now)

            # 2. Generate signals using bars up to (and including) THIS bar.
            slice_bars = {sym: df.loc[:ts] for sym, df in bars.items()}
            signals = self.strategy.generate_signals(slice_bars)

            # 3. Schedule entries: filled at NEXT bar's open with slippage.
            if i + 1 < len(all_idx):
                next_ts = all_idx[i + 1]
                for sig in signals:
                    self._schedule_entry(sig, next_ts, bars, atrs, ts)

            # 4. Mark equity at THIS bar's close.
            self._equity_curve.append((ts, self._mark_to_market(ts, bars)))

        # Force-close any open positions at the last bar's close.
        last_ts = all_idx[-1]
        for sym, pos in list(self._open.items()):
            last_close = float(bars[sym].loc[last_ts, "close"])
            self._close_position(pos, last_ts, last_close)

        equity = pd.Series(dict(self._equity_curve), name="equity")
        returns = equity.pct_change().fillna(0.0)
        return BacktestResult(
            equity=equity, returns=returns, trades=self._trades, warnings=self._warnings
        )

    # --- internals ---

    def _schedule_entry(
        self,
        sig: Signal,
        fill_ts: pd.Timestamp,
        bars: dict[str, pd.DataFrame],
        atrs: dict[str, pd.Series],
        signal_ts: pd.Timestamp,
    ) -> None:
        if sig.side != "buy":
            self._warnings.append(
                f"v1 engine is long-only; ignoring sell signal {sig.symbol}@{signal_ts}"
            )
            return
        df = bars.get(sig.symbol)
        if df is None or fill_ts not in df.index:
            return
        if sig.symbol in self._open:
            return  # one position per symbol in v1

        bar = df.loc[fill_ts]
        atr_now = float(atrs[sig.symbol].loc[signal_ts] or 0.0)
        slip = atr_now * float(self.costs.slip_atr_mult)
        fill_price = float(bar["open"]) + slip

        # Sanity: 0.5% from intra-bar H/L.
        h, low = float(bar["high"]), float(bar["low"])
        if not (low - 0.005 * float(bar["open"]) <= fill_price <= h + 0.005 * float(bar["open"])):
            self._warnings.append(f"implausible fill {sig.symbol}@{fill_ts}: {fill_price:.2f}")

        # Sizing: defer to the live risk module so backtest math == live math byte-for-byte.
        s = get_settings()
        equity_now = self._mark_to_market(signal_ts, bars)
        try:
            qty = position_size(
                equity=Decimal(str(equity_now)),
                risk_pct=s.MAX_PER_TRADE_RISK,
                entry=Decimal(str(fill_price)),
                stop=Decimal(str(sig.stop)),
                max_position_pct=s.MAX_SINGLE_POSITION,
            )
        except (ValueError, ArithmeticError):
            return
        if qty <= 0:
            return

        notional = qty * fill_price
        commission = float(self.costs.commission(qty))
        if notional + commission > self.cash:
            return  # not enough cash

        self.cash -= notional + commission
        self._open[sig.symbol] = _OpenPosition(
            symbol=sig.symbol,
            qty=qty,
            entry_ts=fill_ts,
            entry_price=fill_price,
            stop=float(sig.stop),
            target=float(sig.target) if sig.target else None,
            strategy_tag=sig.strategy_tag,
        )

    def _maybe_exit(
        self, pos: _OpenPosition, ts: pd.Timestamp, bar: pd.Series, atr_now: float
    ) -> None:
        h, low = float(bar["high"]), float(bar["low"])
        slip = atr_now * float(self.costs.slip_atr_mult)
        # Stop fires first if both touched (conservative).
        if low <= pos.stop:
            self._close_position(pos, ts, pos.stop - slip)
            return
        if pos.target is not None and h >= pos.target:
            self._close_position(pos, ts, pos.target - slip)

    def _close_position(self, pos: _OpenPosition, ts: pd.Timestamp, fill_price: float) -> None:
        commission = float(self.costs.commission(pos.qty))
        self.cash += pos.qty * fill_price - commission
        entry_commission = float(self.costs.commission(pos.qty))
        gross = (fill_price - pos.entry_price) * pos.qty
        net_pnl = gross - entry_commission - commission
        self._trades.append(
            TradeRecord(
                symbol=pos.symbol,
                side="buy",
                entry_ts=pos.entry_ts,
                exit_ts=ts,
                qty=pos.qty,
                entry_price=pos.entry_price,
                exit_price=fill_price,
                pnl=net_pnl,
                strategy_tag=pos.strategy_tag,
            )
        )
        del self._open[pos.symbol]

    def _mark_to_market(self, ts: pd.Timestamp, bars: dict[str, pd.DataFrame]) -> float:
        equity = self.cash
        for sym, pos in self._open.items():
            df = bars[sym]
            if ts in df.index:
                equity += pos.qty * float(df.loc[ts, "close"])
        return equity
