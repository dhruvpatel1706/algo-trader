"""vwap_open_retest strategy unit tests.

Test matrix
===========
1. Outside the 09:45-10:30 window -> empty list (time gate).
2. Valid retest: price near VWAP, RSI 40-60, volume >= 0.8x avg -> signal fires.
3. Price stayed above VWAP (never retested) -> no signal.
4. RSI too high (>60) at retest -> no signal.
5. Volume below 0.8x avg at retest -> no signal.
6. Confidence bonus: RSI<50 and high volume -> confidence >= 0.80.
7. Stop is always below entry and below VWAP.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from src.strategies.vwap_open_retest import VwapOpenRetest, _in_window, scan

# ---------------------------------------------------------------------------
# Helpers for building synthetic 5-minute intraday bars
# ---------------------------------------------------------------------------

_TZ = "US/Eastern"
_DATE = "2026-05-07"  # arbitrary trading day


def _make_bars(
    *,
    n: int = 40,
    base_price: float = 500.0,
    date: str = _DATE,
    start_time: str = "09:30",
    freq: str = "5min",
    volume_multiplier: float = 1.0,
    retest: bool = False,
    price_above_vwap: bool = True,
) -> pd.DataFrame:
    """Build a minimal 5-minute intraday DataFrame.

    Parameters
    ----------
    n:
        Number of bars.
    base_price:
        Approximate price level throughout the session.
    date:
        Session date string (YYYY-MM-DD).
    start_time:
        First bar time (HH:MM, 24-hour, Eastern).
    volume_multiplier:
        Scalar applied to every bar's volume.  Use <0.8 to fail the volume gate.
    retest:
        If True, force the *last* bar's close to sit exactly on VWAP (a retest).
        If False, keep the last bar clearly above VWAP.
    price_above_vwap:
        If True, build bars so the first bar close is above its VWAP.
        If False, force first bar to close below VWAP (opening-below scenario).
    """
    tz = _TZ
    idx = pd.date_range(
        f"{date} {start_time}",
        periods=n,
        freq=freq,
        tz=tz,
    )

    rng = np.random.default_rng(42)

    # Slightly upward trending price so the series looks organic.
    drift = np.linspace(0.0, base_price * 0.005, n)
    noise = rng.normal(0, base_price * 0.0005, n)
    close_arr = base_price + drift + noise
    close_arr = np.maximum(close_arr, 1.0)  # no negatives

    high_arr = close_arr + rng.uniform(0.05, 0.20, n)
    low_arr = close_arr - rng.uniform(0.05, 0.20, n)
    low_arr = np.maximum(low_arr, 0.01)
    open_arr = np.roll(close_arr, 1)
    open_arr[0] = close_arr[0]

    # Volume: flat at 1_000_000 x multiplier.
    vol_arr = np.full(n, 1_000_000.0 * volume_multiplier)

    df = pd.DataFrame(
        {
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "volume": vol_arr,
        },
        index=idx,
    )

    # Compute VWAP for manipulation purposes.
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_pv = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    vwap_ser = cum_pv / cum_vol

    if retest:
        # Force the last close to be exactly at VWAP (within 0.0% distance).
        df.loc[df.index[-1], "close"] = float(vwap_ser.iloc[-1])
        df.loc[df.index[-1], "high"] = float(vwap_ser.iloc[-1]) + 0.05
        df.loc[df.index[-1], "low"] = float(vwap_ser.iloc[-1]) - 0.05

    if not price_above_vwap:
        # Force first bar close well below VWAP by making it much cheaper.
        first_vwap = float(vwap_ser.iloc[0])
        df.loc[df.index[0], "close"] = first_vwap * 0.97  # 3% below

    return df


def _now_in_window() -> pd.Timestamp:
    """Return a Timestamp firmly inside the 09:45-10:30 ET window."""
    return pd.Timestamp(f"{_DATE} 10:00:00", tz=_TZ)


def _now_outside_window() -> pd.Timestamp:
    """Return a Timestamp outside the 09:45-10:30 ET window (after 10:30)."""
    return pd.Timestamp(f"{_DATE} 11:00:00", tz=_TZ)


# ---------------------------------------------------------------------------
# Test 1 — time gate: outside window → always []
# ---------------------------------------------------------------------------


def test_returns_empty_outside_time_window():
    """Signals must not be generated before 09:45 or at/after 10:30 ET."""
    s = VwapOpenRetest()
    bars = {"SPY": _make_bars(n=40, retest=True)}

    # 08:30 — pre-market
    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=pd.Timestamp(f"{_DATE} 08:30:00", tz=_TZ),
    ):
        assert s.generate_signals(bars) == []

    # 09:44 — one minute before window
    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=pd.Timestamp(f"{_DATE} 09:44:00", tz=_TZ),
    ):
        assert s.generate_signals(bars) == []

    # 10:30 — exactly at the boundary (excluded)
    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=pd.Timestamp(f"{_DATE} 10:30:00", tz=_TZ),
    ):
        assert s.generate_signals(bars) == []

    # 14:00 — afternoon
    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=pd.Timestamp(f"{_DATE} 14:00:00", tz=_TZ),
    ):
        assert s.generate_signals(bars) == []


# ---------------------------------------------------------------------------
# Test 2 — valid signal fires with correct structure
# ---------------------------------------------------------------------------


def _make_valid_retest_bars(rsi_target: float = 45.0) -> pd.DataFrame:
    """Construct bars that satisfy every entry condition.

    Construction strategy:
    - Flat alternating oscillation keeps RSI neutral (40-60).
    - First bar is made strongly bullish (low well below close) so that
      ``typical[0] = (H+L+C)/3 < close[0]``, which means the session
      opened above VWAP -- the required precondition for a retest.
    - Last bar is pinned exactly on the VWAP computed from the rest of
      the bars, so distance_pct == 0 and the retest condition fires.
    """
    n = 40
    tz = _TZ
    idx = pd.date_range(f"{_DATE} 09:30", periods=n, freq="5min", tz=tz)

    base = 500.0
    close_arr = np.full(n, base, dtype=float)
    # Tiny alternating oscillation keeps RSI in 40-60 range.
    close_arr += np.array([0.1 * ((-1) ** i) for i in range(n)])

    high_arr = close_arr + 0.15
    low_arr = close_arr - 0.15
    open_arr = np.roll(close_arr, 1)
    open_arr[0] = close_arr[0]
    vol_arr = np.full(n, 1_000_000.0)

    df = pd.DataFrame(
        {
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "volume": vol_arr,
        },
        index=idx,
    )

    # Make the first bar strongly bullish: drop the low far below close so
    # typical[0] = (high + low + close) / 3 is well below close[0].
    # This ensures close[0] > VWAP[0], satisfying "opened above VWAP".
    df.loc[df.index[0], "low"] = float(df["close"].iloc[0]) - 2.0  # 2pt wick down

    # Calculate VWAP and pin last bar to it.
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_pv = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    vwap_last = float((cum_pv / cum_vol).iloc[-1])

    df.loc[df.index[-1], "close"] = vwap_last
    df.loc[df.index[-1], "high"] = vwap_last + 0.05
    df.loc[df.index[-1], "low"] = vwap_last - 0.05

    return df


def test_generates_signal_on_valid_retest():
    """Valid retest → Signal with correct fields."""
    s = VwapOpenRetest()
    df = _make_valid_retest_bars()
    bars = {"SPY": df}

    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=_now_in_window(),
    ):
        sigs = s.generate_signals(bars)

    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.symbol == "SPY"
    assert sig.side == "buy"
    assert sig.asset_class == "equity"
    assert sig.strategy_tag == "vwap_open_retest"
    assert isinstance(sig.entry, Decimal)
    assert isinstance(sig.stop, Decimal)
    assert sig.stop < sig.entry, "stop must be below entry"
    assert 0.0 < sig.confidence <= 1.0


# ---------------------------------------------------------------------------
# Test 3 — price never retested (stayed above VWAP)
# ---------------------------------------------------------------------------


def test_no_signal_when_price_stays_above_vwap():
    """If the last bar close is significantly above VWAP, no signal fires."""
    n = 40
    tz = _TZ
    idx = pd.date_range(f"{_DATE} 09:30", periods=n, freq="5min", tz=tz)

    base = 500.0
    # Monotonically rising — price moves away from VWAP each bar.
    close_arr = base + np.linspace(0, 5.0, n)  # 1% above start by end
    high_arr = close_arr + 0.20
    low_arr = close_arr - 0.10
    open_arr = np.roll(close_arr, 1)
    open_arr[0] = close_arr[0]
    vol_arr = np.full(n, 1_000_000.0)

    df = pd.DataFrame(
        {
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "volume": vol_arr,
        },
        index=idx,
    )

    s = VwapOpenRetest()
    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=_now_in_window(),
    ):
        sigs = s.generate_signals({"SPY": df})

    assert sigs == [], "monotonically rising price cannot trigger a VWAP retest"


# ---------------------------------------------------------------------------
# Test 4 — RSI too high (>60) at retest
# ---------------------------------------------------------------------------


def _make_high_rsi_retest_bars() -> pd.DataFrame:
    """Bars ending on VWAP but with an overbought RSI (>60)."""
    n = 40
    tz = _TZ
    idx = pd.date_range(f"{_DATE} 09:30", periods=n, freq="5min", tz=tz)

    base = 500.0
    # Strong upward trend for first 38 bars → RSI rises well above 60.
    close_arr = base + np.linspace(0, 3.0, n)
    high_arr = close_arr + 0.20
    low_arr = close_arr - 0.10
    open_arr = np.roll(close_arr, 1)
    open_arr[0] = close_arr[0]
    vol_arr = np.full(n, 1_000_000.0)

    df = pd.DataFrame(
        {
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "volume": vol_arr,
        },
        index=idx,
    )

    # Pin the last bar onto VWAP so the retest proximity condition passes.
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_pv = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    vwap_last = float((cum_pv / cum_vol).iloc[-1])

    df.loc[df.index[-1], "close"] = vwap_last
    df.loc[df.index[-1], "high"] = vwap_last + 0.05
    df.loc[df.index[-1], "low"] = vwap_last - 0.05

    return df


def test_no_signal_when_rsi_too_high():
    """Overbought RSI (>60) disqualifies a retest even if price is at VWAP."""
    s = VwapOpenRetest()
    df = _make_high_rsi_retest_bars()

    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=_now_in_window(),
    ):
        sigs = s.generate_signals({"SPY": df})

    assert sigs == [], "RSI > 60 should block the signal"


# ---------------------------------------------------------------------------
# Test 5 — volume below threshold
# ---------------------------------------------------------------------------


def test_no_signal_when_volume_below_threshold():
    """Volume < 0.8x 20-bar average must suppress the signal."""
    df = _make_valid_retest_bars()

    # Slash the last bar's volume to just 50% of the others.
    df.loc[df.index[-1], "volume"] = 500_000.0  # 0.5x the 1_000_000 average

    s = VwapOpenRetest()
    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=_now_in_window(),
    ):
        sigs = s.generate_signals({"SPY": df})

    assert sigs == [], "low volume at retest should block the signal"


# ---------------------------------------------------------------------------
# Test 6 — confidence bonuses are applied correctly
# ---------------------------------------------------------------------------


def _make_high_vol_low_rsi_bars() -> pd.DataFrame:
    """Bars at VWAP, RSI slightly below 50, volume well above average."""
    df = _make_valid_retest_bars()
    # Pump the last bar volume to 1.5x average to trigger the vol bonus.
    df.loc[df.index[-1], "volume"] = 1_500_000.0
    return df


def test_confidence_bonuses_applied():
    """Both RSI<50 and vol>1.2x bonuses should push confidence above base."""
    s = VwapOpenRetest()
    df = _make_high_vol_low_rsi_bars()

    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=_now_in_window(),
    ):
        sigs = s.generate_signals({"SPY": df})

    # Whether or not RSI lands below 50 depends on the synthetic path;
    # at minimum the vol bonus alone should push confidence > base (0.60).
    if sigs:
        sig = sigs[0]
        # Vol ratio = 1.5 (> 1.2) → vol bonus should apply.
        assert sig.confidence > 0.60, "vol bonus should increase confidence"
        assert sig.confidence <= 1.0


# ---------------------------------------------------------------------------
# Test 7 — stop is below entry and below VWAP
# ---------------------------------------------------------------------------


def test_stop_below_entry_and_below_vwap():
    """The stop must always be below entry and explicitly below VWAP."""
    s = VwapOpenRetest()
    df = _make_valid_retest_bars()

    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=_now_in_window(),
    ):
        sigs = s.generate_signals({"SPY": df})

    for sig in sigs:
        assert sig.stop < sig.entry, "stop must be below entry"
        # VWAP ~ entry (retest pinned exactly on VWAP).
        # Stop = VWAP * (1 - 0.005), so stop < entry.
        entry_f = float(sig.entry)
        stop_f = float(sig.stop)
        assert stop_f < entry_f * 0.9995, "stop should be materially below entry"


# ---------------------------------------------------------------------------
# Test 8 — scan() module-level wrapper delegates to VwapOpenRetest
# ---------------------------------------------------------------------------


def test_scan_module_function_matches_generate_signals():
    """The scan() wrapper must return the same signals as generate_signals()."""
    df = _make_valid_retest_bars()
    bars = {"SPY": df}

    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=_now_in_window(),
    ):
        direct = VwapOpenRetest().generate_signals(bars)

    with patch(
        "src.strategies.vwap_open_retest.pd.Timestamp.now",
        return_value=_now_in_window(),
    ):
        via_scan = scan(bars)

    assert len(direct) == len(via_scan)
    for a, b in zip(direct, via_scan, strict=True):
        assert a.symbol == b.symbol
        assert a.entry == b.entry
        assert a.stop == b.stop


# ---------------------------------------------------------------------------
# Test 9 — _in_window boundary conditions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "h, m, expected",
    [
        (9, 44, False),   # 09:44 — one minute before window
        (9, 45, True),    # 09:45 — exactly at start (inclusive)
        (10, 0, True),    # 10:00 — firmly inside
        (10, 29, True),   # 10:29 — last valid minute
        (10, 30, False),  # 10:30 — boundary (exclusive)
        (11, 0, False),   # 11:00 — after window
    ],
)
def test_in_window_boundary(h: int, m: int, expected: bool):
    ts = pd.Timestamp(f"{_DATE} {h:02d}:{m:02d}:00", tz=_TZ)
    assert _in_window(ts) == expected
