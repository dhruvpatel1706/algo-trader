"""funding_rate_divergence strategy unit tests.

Long-only path: fires when
  1. Most recent 8h funding rate is at most ``funding_threshold`` (default
     -0.0003 = -0.03%/8h),
  2. RSI(14) is below ``rsi_max`` (default 35),
  3. close is within ``bb_buffer_pct`` (default 1.5%) above the lower
     Bollinger band.

Funding data is injected via the ``funding_fetcher`` constructor kwarg so
tests stay deterministic — production picks up the default
``src.data.funding.fetch_funding_rate`` automatically.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from src.strategies import load_strategy
from src.strategies.funding_rate_divergence import (
    FundingRateDivergence,
    FundingRateDivergenceParams,
)


def _oversold_at_lower_bb(n: int = 80) -> pd.DataFrame:
    """Construct a series whose last bar is RSI-oversold and sitting just
    above the lower Bollinger band.

    Recipe: a 60-bar drift up (so BB has a real spread) followed by a
    20-bar slide down — the slide builds a low RSI and pushes price below
    the BB mid. With std=2.0 BB, the slide takes price to within the
    lower-band region.
    """
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    closes = np.zeros(n)
    drift_end = 60
    closes[:drift_end] = 100.0 + np.arange(drift_end) * 0.4  # up to ~123
    # slide down from the drift peak — fast enough to drive RSI under 35
    slide_start = closes[drift_end - 1]
    closes[drift_end:] = slide_start - np.arange(n - drift_end) * 0.6
    # Tiny noise so the series isn't perfectly deterministic but doesn't
    # break the BB geometry.
    rng = np.random.default_rng(7)
    closes += rng.normal(0.0, 0.05, n)
    close = pd.Series(closes, index=idx, dtype=float)
    high = close + 0.4
    low = close - 0.4
    volume = pd.Series(np.full(n, 1_000_000.0), index=idx)
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume}
    )


def _flat_no_setup(n: int = 80) -> pd.DataFrame:
    """Quiet sideways series — RSI hovers around 50, BB is tight, close is
    nowhere near the lower band. No setup."""
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    rng = np.random.default_rng(11)
    closes = 100.0 + np.cumsum(rng.normal(0.0, 0.15, n))
    close = pd.Series(closes, index=idx, dtype=float)
    high = close + 0.3
    low = close - 0.3
    volume = pd.Series(np.full(n, 1_000_000.0), index=idx)
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume}
    )


def _funding_fetcher_value(value: float) -> object:
    """Build a fetcher that returns a single-row funding DataFrame with
    the given rate as the latest print. Captures call args for assertions."""
    calls: list[tuple[str, date, date]] = []

    def fetcher(symbol: str, start: date, end: date) -> pd.DataFrame:
        calls.append((symbol, start, end))
        return pd.DataFrame(
            {"funding_rate": [value], "predicted_rate": [None]},
            index=pd.to_datetime(["2024-01-08T08:00:00Z"]),
        )

    fetcher.calls = calls  # type: ignore[attr-defined]
    return fetcher


def _empty_funding_fetcher() -> object:
    """Fetcher that returns an empty frame — simulates a total-failure
    fallback chain (Binance/Bybit/OKX all unreachable)."""

    def fetcher(symbol: str, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame(columns=["funding_rate", "predicted_rate"])

    return fetcher


# ---------------------------------------------------------------------------
# Trigger / no-trigger
# ---------------------------------------------------------------------------


def test_long_signal_when_funding_negative_and_rsi_oversold_at_bb():
    df = _oversold_at_lower_bb()
    s = FundingRateDivergence(
        funding_fetcher=_funding_fetcher_value(-0.0005),  # below default -0.0003
    )
    sigs = s.generate_signals({"BTCUSDT": df})
    assert len(sigs) == 1, f"expected 1 long signal, got {sigs}"
    sig = sigs[0]
    assert sig.symbol == "BTCUSDT"
    assert sig.side == "buy"
    assert float(sig.stop) < float(sig.entry)
    risk = float(sig.entry) - float(sig.stop)
    expected_target = float(sig.entry) + 2.0 * risk
    assert abs(float(sig.target) - expected_target) < 0.01
    assert sig.strategy_tag == "funding_rate_divergence"


def test_no_signal_when_funding_above_threshold():
    """Funding rate not deeply negative → setup invalidated even if RSI
    and BB look perfect."""
    df = _oversold_at_lower_bb()
    s = FundingRateDivergence(
        funding_fetcher=_funding_fetcher_value(-0.0001),  # above -0.0003
    )
    assert s.generate_signals({"BTCUSDT": df}) == []


def test_no_signal_when_rsi_not_oversold():
    """Quiet flat regime keeps RSI around 50 → no signal regardless of
    funding extreme."""
    df = _flat_no_setup()
    s = FundingRateDivergence(
        funding_fetcher=_funding_fetcher_value(-0.0010),  # very crowded short
    )
    assert s.generate_signals({"BTCUSDT": df}) == []


def test_no_signal_when_funding_data_empty():
    """All venues failed (Binance/Bybit/OKX geo-block) → strategy must
    skip the symbol, never crash."""
    df = _oversold_at_lower_bb()
    s = FundingRateDivergence(funding_fetcher=_empty_funding_fetcher())
    assert s.generate_signals({"BTCUSDT": df}) == []


def test_no_signal_when_funding_fetcher_raises():
    """Fetcher exception (network error, parse error) must not propagate
    — log + skip."""
    df = _oversold_at_lower_bb()

    def bad(symbol: str, start: date, end: date) -> pd.DataFrame:
        raise RuntimeError("simulated network failure")

    s = FundingRateDivergence(funding_fetcher=bad)
    assert s.generate_signals({"BTCUSDT": df}) == []


def test_short_history_no_signal():
    """Series shorter than the BB warm-up returns nothing rather than
    crashing. Use the flat-no-setup fixture with a small n — its shape is
    independent of the drift/slide phases used by the trigger fixture."""
    df = _flat_no_setup(n=15)
    s = FundingRateDivergence(funding_fetcher=_funding_fetcher_value(-0.0005))
    assert s.generate_signals({"BTCUSDT": df}) == []


# ---------------------------------------------------------------------------
# Parameter sensitivity
# ---------------------------------------------------------------------------


def test_tighter_funding_threshold_filters_marginal_setups():
    """A funding rate of -0.0004 catches the default threshold but should
    fail when we tighten to -0.0008."""
    df = _oversold_at_lower_bb()
    fetcher = _funding_fetcher_value(-0.0004)
    # Default threshold -0.0003 catches it.
    assert len(FundingRateDivergence(funding_fetcher=fetcher).generate_signals(
        {"BTCUSDT": df}
    )) == 1
    # Tighter threshold -0.0008 rejects it.
    strict = FundingRateDivergence(
        FundingRateDivergenceParams(funding_threshold=-0.0008),
        funding_fetcher=fetcher,
    )
    assert strict.generate_signals({"BTCUSDT": df}) == []


def test_funding_fetcher_invoked_with_symbol_and_lookback():
    """Pin the fetcher contract: fetcher gets the symbol unchanged and a
    lookback window of ``funding_lookback_days``."""
    df = _oversold_at_lower_bb()
    fetcher = _funding_fetcher_value(-0.0005)
    s = FundingRateDivergence(
        FundingRateDivergenceParams(funding_lookback_days=10),
        funding_fetcher=fetcher,
    )
    s.generate_signals({"BTCUSDT": df})
    assert len(fetcher.calls) == 1  # type: ignore[attr-defined]
    sym, start, end = fetcher.calls[0]  # type: ignore[attr-defined]
    assert sym == "BTCUSDT"
    # window is 10 days back, ending tomorrow (inclusive on start, exclusive on end)
    assert (end - start).days == 11


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_funding_rate_divergence_loads_via_registry():
    s = load_strategy("funding_rate_divergence")
    assert isinstance(s, FundingRateDivergence)
    universe = s.universe()
    # Wired to crypto_majors in docs/universes.yaml.
    assert "BTCUSDT" in universe
    assert "ETHUSDT" in universe
    assert all(sym.endswith("USDT") for sym in universe)


def test_funding_rate_divergence_default_params_match_proposal():
    """Pin the defaults to the Researcher's proposal in
    docs/improvements/strategies/funding_rate_divergence.md so backtest
    results stay reproducible."""
    p = FundingRateDivergenceParams()
    assert p.funding_threshold == -0.0003
    assert p.rsi_period == 14
    assert p.rsi_max == 35.0
    assert p.bb_period == 20
    assert p.bb_std == 2.0
    assert p.bb_buffer_pct == 0.015
    assert p.target_r == 2.0
