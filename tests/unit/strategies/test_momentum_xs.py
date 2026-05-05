"""momentum_xs strategy unit tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
from src.strategies import load_strategy
from src.strategies.momentum_xs import MomentumXs

# 12 months * 21 bars + 21 skip + warmup -> needs >= ~315 bars to fire.
_DEFAULT_BARS = 360


def _series_with_total_return(total_return: float, n: int = _DEFAULT_BARS, start: float = 100.0,
                              end_date: str = "2024-08-01") -> pd.DataFrame:
    """Build a smooth-drift OHLCV frame ending on `end_date` with the given
    cumulative log-uniform return (recent ~21 days held flat so the 12-1 number
    matches `total_return` to a good approximation)."""
    end = pd.Timestamp(end_date)
    idx = pd.bdate_range(end=end, periods=n)

    # Want price[-22] / price[-22 - 252] - 1 == total_return.
    # Build a linear ramp over the relevant 252-bar window, flat tail on skip.
    prices = np.full(n, float(start))
    skip = 21
    lookback = 252
    # Indices: `n-1-skip` is the recent reference, `n-1-skip-lookback` is past.
    past_idx = n - 1 - skip - lookback
    recent_idx = n - 1 - skip
    # Linear ramp from past_idx -> recent_idx so close[recent]/close[past] == 1+r
    target_recent = start * (1.0 + total_return)
    ramp = np.linspace(start, target_recent, recent_idx - past_idx + 1)
    prices[past_idx : recent_idx + 1] = ramp
    # Anything before past_idx stays flat at `start`.
    prices[:past_idx] = start
    # Recent 21 bars flat at target_recent so r_12_1 == total_return exactly.
    prices[recent_idx + 1 :] = target_recent

    close = pd.Series(prices, index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def _ensure_month_boundary(df: pd.DataFrame) -> pd.DataFrame:
    """Trim trailing rows so the last bar is the first trading day of its month."""
    idx = df.index
    # Walk backward until prev-bar's month differs from last bar's month.
    cut = len(idx)
    while cut >= 2 and idx[cut - 1].month == idx[cut - 2].month:
        cut -= 1
    return df.iloc[:cut]


def _build_universe_bars(returns: list[float]) -> dict[str, pd.DataFrame]:
    """Build a 50-symbol bars dict with the given list of 12-1 returns."""
    assert len(returns) == 50
    syms = [f"SYM{i:02d}" for i in range(50)]
    bars = {}
    for sym, r in zip(syms, returns, strict=True):
        df = _series_with_total_return(r)
        df = _ensure_month_boundary(df)
        bars[sym] = df
    return bars


def test_momentum_xs_loads_via_registry():
    s = load_strategy("momentum_xs")
    assert isinstance(s, MomentumXs)
    universe = s.universe()
    # large_caps_50 is the assigned universe.
    assert len(universe) >= 40
    assert "AAPL" in universe
    assert "NVDA" in universe


def test_momentum_xs_top_decile_signals_on_month_boundary():
    # Spread of 12-1 returns from -0.30 to +0.70 across 50 names.
    returns = list(np.linspace(-0.30, 0.70, 50))
    bars = _build_universe_bars(returns)
    # Sanity: confirm the last bar of every frame is a month boundary.
    sample_idx = next(iter(bars.values())).index
    assert sample_idx[-1].month != sample_idx[-2].month

    s = MomentumXs()
    sigs = s.generate_signals(bars)

    # Top decile of 50 == 5 signals.
    assert len(sigs) == 5

    # The top 5 names by return should be SYM45..SYM49 (highest returns).
    signal_syms = {sig.symbol for sig in sigs}
    expected = {"SYM45", "SYM46", "SYM47", "SYM48", "SYM49"}
    assert signal_syms == expected


def test_momentum_xs_returns_no_signals_mid_month():
    returns = list(np.linspace(-0.30, 0.70, 50))
    bars = _build_universe_bars(returns)

    # Drop the trailing bar so we land somewhere mid-month rather than on a
    # month-boundary bar. Same input data otherwise.
    mid_bars = {sym: df.iloc[:-1] for sym, df in bars.items()}
    last_idx = next(iter(mid_bars.values())).index
    # Make absolutely sure we're not on a boundary by walking back a few bars.
    while last_idx[-1].month != last_idx[-2].month:
        mid_bars = {sym: df.iloc[:-1] for sym, df in mid_bars.items()}
        last_idx = next(iter(mid_bars.values())).index

    s = MomentumXs()
    assert s.generate_signals(mid_bars) == []


def test_momentum_xs_insufficient_history_returns_no_signals():
    # 100 bars is far below the ~315-bar warmup requirement.
    short_idx = pd.bdate_range(end="2024-08-01", periods=100)
    short_df = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000,
        },
        index=short_idx,
    )
    short_df = _ensure_month_boundary(short_df)
    s = MomentumXs()
    assert s.generate_signals({"AAPL": short_df, "MSFT": short_df.copy()}) == []


def test_momentum_xs_signals_have_valid_brackets_and_confidence():
    returns = list(np.linspace(-0.30, 0.70, 50))
    bars = _build_universe_bars(returns)

    s = MomentumXs()
    sigs = s.generate_signals(bars)
    assert len(sigs) > 0

    for sig in sigs:
        # Stop below entry, target above entry.
        assert sig.stop < sig.entry, f"{sig.symbol}: stop {sig.stop} >= entry {sig.entry}"
        assert sig.target is not None and sig.target > sig.entry, (
            f"{sig.symbol}: target {sig.target} <= entry {sig.entry}"
        )
        # Confidence in [0, 1] (Signal.__post_init__ also enforces this).
        assert 0.0 <= sig.confidence <= 1.0
        assert sig.strategy_tag == "momentum_xs"
        assert sig.side == "buy"


def test_momentum_xs_confidence_decays_with_rank():
    returns = list(np.linspace(-0.30, 0.70, 50))
    bars = _build_universe_bars(returns)

    s = MomentumXs()
    sigs = s.generate_signals(bars)
    # Order by descending return (= signal rank order).
    ranked = sorted(sigs, key=lambda sg: float(sg.entry), reverse=True)
    confidences = [sg.confidence for sg in ranked]
    # Rank-1 confidence should be >= last-rank confidence.
    assert confidences[0] >= confidences[-1]
