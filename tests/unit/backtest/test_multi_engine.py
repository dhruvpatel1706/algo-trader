"""Unit tests for `src.backtest.multi_engine.MultiStrategyEngine`."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from src.backtest.engine import BacktestResult
from src.backtest.multi_engine import MultiBacktestResult, MultiStrategyEngine
from src.strategies.base import Signal, Strategy


class _BuyOnceOn(Strategy):
    """Buy `symbol` exactly once when the slice has `trigger_len` bars.

    Used to make multi-strategy traffic deterministic in tests.
    """

    def __init__(self, name: str, symbol: str, trigger_len: int = 5) -> None:
        self.name = name
        self.symbol = symbol
        self.trigger_len = trigger_len

    def universe(self):
        return (self.symbol,)

    def generate_signals(self, bars):
        df = bars.get(self.symbol)
        if df is None or len(df) != self.trigger_len:
            return []
        last = df.iloc[-1]
        return [
            Signal(
                symbol=self.symbol,
                side="buy",
                entry=Decimal(str(last["close"])),
                stop=Decimal(str(float(last["close"]) * 0.98)),
                target=Decimal(str(float(last["close"]) * 1.05)),
                confidence=0.6,
                strategy_tag=self.name,
                timestamp=last.name,
            )
        ]


class _Mute(Strategy):
    """Strategy that never fires."""

    def __init__(self, name: str = "mute", symbol: str = "SPY") -> None:
        self.name = name
        self.symbol = symbol

    def universe(self):
        return (self.symbol,)

    def generate_signals(self, bars):
        return []


def _bars(symbol: str, n: int = 30, start: float = 100.0, drift: float = 0.05, seed: int = 0):
    """Build a simple synthetic OHLCV frame with mild upward drift."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(start + np.cumsum(rng.normal(drift, 0.5, n)), index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(start)
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


# --- tests ---


def test_two_strategy_run_produces_aggregated_result():
    """Two strategies each fire once on different symbols; multi-engine returns
    a single aggregated MultiBacktestResult with both per-strategy results."""
    bars = {"SPY": _bars("SPY", n=30, seed=1), "QQQ": _bars("QQQ", n=30, seed=2)}
    multi = MultiStrategyEngine(
        strategies=[
            _BuyOnceOn("alpha", "SPY", trigger_len=5),
            _BuyOnceOn("beta", "QQQ", trigger_len=5),
        ],
        starting_equity=Decimal("100000"),
    )
    result = multi.run(bars)

    assert isinstance(result, MultiBacktestResult)
    assert set(result.per_strategy.keys()) == {"alpha", "beta"}
    assert all(isinstance(v, BacktestResult) for v in result.per_strategy.values())
    # Joined equity is non-empty and starts at the seed.
    assert not result.equity.empty
    assert result.equity.iloc[0] == pytest.approx(100_000.0, rel=1e-6)
    # Both strategies should have produced exactly one trade each.
    assert len(result.trades) == 2
    tags = {t.strategy_tag for t in result.trades}
    assert tags == {"alpha", "beta"}


def test_shared_equity_starts_equal_to_starting_equity():
    """Joined equity at t0 must equal starting_equity regardless of N strategies."""
    bars = {"SPY": _bars("SPY", n=20, seed=3)}
    for n in (1, 2, 4):
        strategies = [_Mute(name=f"m{i}", symbol="SPY") for i in range(n)]
        multi = MultiStrategyEngine(strategies, starting_equity=Decimal("100000"))
        result = multi.run(bars)
        # All strategies are mutes => no trades, equity stays flat at starting equity.
        assert result.equity.iloc[0] == pytest.approx(100_000.0)
        assert result.equity.iloc[-1] == pytest.approx(100_000.0)


def test_pairwise_correlation_matrix_populated():
    """With >= 2 strategies, the correlation matrix is square N x N with unit diagonal."""
    bars = {
        "SPY": _bars("SPY", n=40, seed=11),
        "QQQ": _bars("QQQ", n=40, seed=12),
        "IWM": _bars("IWM", n=40, seed=13),
    }
    multi = MultiStrategyEngine(
        strategies=[
            _BuyOnceOn("a", "SPY", trigger_len=5),
            _BuyOnceOn("b", "QQQ", trigger_len=6),
            _BuyOnceOn("c", "IWM", trigger_len=7),
        ],
        starting_equity=Decimal("100000"),
    )
    result = multi.run(bars)
    cm = result.correlation_matrix
    assert cm.shape == (3, 3)
    assert list(cm.columns) == ["a", "b", "c"]
    # Diagonal is 1.0 by construction.
    for name in cm.columns:
        assert cm.loc[name, name] == pytest.approx(1.0)
    # Off-diagonal is bounded in [-1, 1].
    for i in cm.index:
        for j in cm.columns:
            assert -1.0 - 1e-9 <= float(cm.loc[i, j]) <= 1.0 + 1e-9


def test_strategy_tag_isolation():
    """Trades carry the originating strategy tag; per-strategy results stay separate."""
    bars = {"SPY": _bars("SPY", n=30, seed=21), "QQQ": _bars("QQQ", n=30, seed=22)}
    multi = MultiStrategyEngine(
        strategies=[
            _BuyOnceOn("alpha", "SPY", trigger_len=5),
            _BuyOnceOn("beta", "QQQ", trigger_len=5),
        ],
        starting_equity=Decimal("100000"),
    )
    result = multi.run(bars)

    # Alpha's per-strategy result must contain only alpha trades; same for beta.
    alpha_trades = result.per_strategy["alpha"].trades
    beta_trades = result.per_strategy["beta"].trades
    assert all(t.strategy_tag == "alpha" for t in alpha_trades)
    assert all(t.strategy_tag == "beta" for t in beta_trades)
    assert all(t.symbol == "SPY" for t in alpha_trades)
    assert all(t.symbol == "QQQ" for t in beta_trades)
    # Combined trades on the joined view = union of both.
    assert len(result.trades) == len(alpha_trades) + len(beta_trades)


def test_joined_equity_equals_sum_of_per_strategy_equity():
    """The joined equity series must equal the sum of per-strategy equity at each ts."""
    bars = {"SPY": _bars("SPY", n=30, seed=31), "QQQ": _bars("QQQ", n=30, seed=32)}
    multi = MultiStrategyEngine(
        strategies=[
            _BuyOnceOn("alpha", "SPY", trigger_len=5),
            _BuyOnceOn("beta", "QQQ", trigger_len=5),
        ],
        starting_equity=Decimal("100000"),
    )
    result = multi.run(bars)

    # Reconstruct the per-strategy frame and align on the joined index.
    per = pd.concat(
        [r.equity.rename(name) for name, r in result.per_strategy.items()], axis=1
    ).sort_index()
    per = per.ffill().bfill()
    expected = per.sum(axis=1)
    assert (result.equity.index == expected.index).all()
    np.testing.assert_allclose(result.equity.values, expected.values, rtol=1e-9, atol=1e-9)


def test_contributions_sum_to_total_return():
    """Per-strategy contributions sum to the joined total return."""
    bars = {"SPY": _bars("SPY", n=30, seed=41), "QQQ": _bars("QQQ", n=30, seed=42)}
    multi = MultiStrategyEngine(
        strategies=[
            _BuyOnceOn("alpha", "SPY", trigger_len=5),
            _BuyOnceOn("beta", "QQQ", trigger_len=5),
        ],
        starting_equity=Decimal("100000"),
    )
    result = multi.run(bars)

    total_contrib = sum(result.contributions.values())
    joined_return = float(result.equity.iloc[-1]) / float(result.equity.iloc[0]) - 1.0
    assert total_contrib == pytest.approx(joined_return, abs=1e-9)


def test_empty_strategy_list_rejected():
    with pytest.raises(ValueError):
        MultiStrategyEngine(strategies=[], starting_equity=Decimal("100000"))


def test_duplicate_strategy_names_rejected():
    with pytest.raises(ValueError):
        MultiStrategyEngine(
            strategies=[
                _BuyOnceOn("dup", "SPY"),
                _BuyOnceOn("dup", "QQQ"),
            ]
        )


def test_empty_bars_rejected():
    multi = MultiStrategyEngine(strategies=[_Mute()], starting_equity=Decimal("100000"))
    with pytest.raises(ValueError):
        multi.run({})
