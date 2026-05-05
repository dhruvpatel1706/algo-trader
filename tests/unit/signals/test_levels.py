"""Unit tests for gap detection and level utilities."""

from __future__ import annotations

import pandas as pd
from src.signals.levels import (
    Gap,
    gap_confluence_score,
    nearest_gap_distance_atr,
    unfilled_gaps,
)


def _bars(rows: list[tuple[float, float]], start: str = "2024-01-02") -> pd.DataFrame:
    """Build a minimal OHLC frame from (high, low) pairs on consecutive business days."""
    idx = pd.bdate_range(start=start, periods=len(rows))
    return pd.DataFrame(
        {
            "high": [h for h, _ in rows],
            "low": [low for _, low in rows],
        },
        index=idx,
    )


# ---------- detection ----------


def test_detects_single_up_gap():
    # Bar 0: high=100, low=99. Bar 1: low=102 > 100 -> up gap [100, 102].
    bars = _bars([(100.0, 99.0), (105.0, 102.0), (104.5, 103.0)])
    gaps = unfilled_gaps(bars, lookback=10, min_gap_pct=0.005)
    assert len(gaps) == 1
    g = gaps[0]
    assert g.direction == "up"
    assert g.upper == 102.0
    assert g.lower == 100.0
    assert g.filled is False
    assert g.age_bars == 1  # bar at i=1, n=3 -> n-1-i = 1


def test_detects_single_down_gap():
    # Bar 0: high=110, low=108. Bar 1: high=107 < 108 -> down gap [107, 108].
    bars = _bars([(110.0, 108.0), (107.0, 105.0), (106.5, 105.5)])
    gaps = unfilled_gaps(bars, lookback=10, min_gap_pct=0.005)
    assert len(gaps) == 1
    g = gaps[0]
    assert g.direction == "down"
    assert g.upper == 108.0
    assert g.lower == 107.0


def test_filled_gap_is_excluded():
    # Up gap on bar 1 ([100, 102]); bar 2 dips to 101 -> overlaps gap -> filled.
    bars = _bars(
        [
            (100.0, 99.0),
            (105.0, 102.0),  # up gap [100, 102]
            (104.0, 101.0),  # 101 is inside the gap -> fills it
        ]
    )
    gaps = unfilled_gaps(bars, lookback=10, min_gap_pct=0.005)
    assert gaps == []


def test_subthreshold_gap_not_detected():
    # Only a 0.1% gap, below default 0.5% threshold.
    bars = _bars(
        [
            (100.0, 99.0),
            (101.0, 100.1),  # gap of 0.1 (0.1%) -> filtered
            (101.5, 101.0),
        ]
    )
    assert unfilled_gaps(bars, lookback=10, min_gap_pct=0.005) == []


def test_threshold_just_above_passes():
    # 1% gap should pass a 0.5% threshold.
    bars = _bars(
        [
            (100.0, 99.0),
            (105.0, 101.0),  # 1% gap above 100
            (104.5, 102.5),
        ]
    )
    gaps = unfilled_gaps(bars, lookback=10, min_gap_pct=0.005)
    assert len(gaps) == 1 and gaps[0].direction == "up"


def test_multiple_gaps_in_chronological_order():
    # Two unfilled up-gaps: [100, 102] then [105, 107]. All later bars stay
    # above each gap's upper edge so neither is re-entered.
    bars = _bars(
        [
            (100.0, 99.0),
            (105.0, 102.0),  # up gap [100, 102] @ idx 1
            (104.5, 103.0),  # stays above 102 -> first gap still unfilled
            (110.0, 107.0),  # up gap [105, 107] @ idx 3 (low 107 > prev high 104.5? no -> need)
            (109.5, 108.0),
        ]
    )
    # Re-check: idx 3 prev_high=104.5, cur_low=107 -> gap [104.5, 107]. Good.
    gaps = unfilled_gaps(bars, lookback=10, min_gap_pct=0.005)
    assert len(gaps) == 2
    assert gaps[0].ts < gaps[1].ts
    assert gaps[0].upper == 102.0
    assert gaps[1].lower == 104.5


def test_empty_bars_returns_empty():
    empty = pd.DataFrame({"high": [], "low": []})
    assert unfilled_gaps(empty) == []


def test_single_bar_returns_empty():
    bars = _bars([(100.0, 99.0)])
    assert unfilled_gaps(bars) == []


# ---------- nearest_gap_distance_atr ----------


def _gap(direction: str, lower: float, upper: float) -> Gap:
    return Gap(
        ts=pd.Timestamp("2024-01-02"),
        direction=direction,  # type: ignore[arg-type]
        upper=upper,
        lower=lower,
        age_bars=1,
        filled=False,
    )


def test_nearest_gap_distance_negative_when_below_price():
    # Down gap at [95, 96], price=100, ATR=1.0 -> distance = -(100-96)/1 = -4.0
    gaps = [_gap("down", 95.0, 96.0)]
    d = nearest_gap_distance_atr(price=100.0, gaps=gaps, atr_value=1.0)
    assert d == -4.0


def test_nearest_gap_distance_positive_when_above_price():
    # Up gap at [102, 104], price=100, ATR=2.0 -> distance = (102-100)/2 = +1.0
    gaps = [_gap("up", 102.0, 104.0)]
    d = nearest_gap_distance_atr(price=100.0, gaps=gaps, atr_value=2.0)
    assert d == 1.0


def test_nearest_gap_distance_none_when_far_away():
    # Gap 30 ATR below -> beyond 20 ATR cutoff.
    gaps = [_gap("down", 50.0, 51.0)]
    assert nearest_gap_distance_atr(price=100.0, gaps=gaps, atr_value=1.0) is None


def test_nearest_gap_distance_none_when_no_gaps():
    assert nearest_gap_distance_atr(price=100.0, gaps=[], atr_value=1.0) is None


def test_nearest_gap_distance_picks_closest():
    # One gap close above, one far below -> closer wins, signed positive.
    gaps = [_gap("down", 80.0, 81.0), _gap("up", 102.0, 104.0)]
    d = nearest_gap_distance_atr(price=100.0, gaps=gaps, atr_value=1.0)
    assert d == 2.0


# ---------- gap_confluence_score ----------


def test_long_score_high_when_down_gap_just_below():
    # Down gap top at 99.9, price 100, ATR=2 -> dist = 0.05 ATR -> score ~= 0.9
    gaps = [_gap("down", 99.0, 99.9)]
    score = gap_confluence_score(price=100.0, gaps=gaps, atr_value=2.0, direction="long")
    assert 0.85 < score <= 1.0


def test_long_score_zero_when_down_gap_too_far():
    # Down gap top at 95, price 100, ATR=1 -> dist = 5 ATR (> 0.5) -> 0
    gaps = [_gap("down", 94.0, 95.0)]
    score = gap_confluence_score(price=100.0, gaps=gaps, atr_value=1.0, direction="long")
    assert score == 0.0


def test_long_ignores_up_gap_above_price():
    # Up gap above price doesn't help a long.
    gaps = [_gap("up", 100.05, 100.1)]
    score = gap_confluence_score(price=100.0, gaps=gaps, atr_value=1.0, direction="long")
    assert score == 0.0


def test_short_score_high_when_up_gap_just_above():
    # Up gap bottom at 100.05, price 100, ATR=2 -> dist = 0.025 ATR -> score ~= 0.95
    gaps = [_gap("up", 100.05, 100.5)]
    score = gap_confluence_score(price=100.0, gaps=gaps, atr_value=2.0, direction="short")
    assert score > 0.9


def test_long_score_higher_than_short_for_supportive_down_gap():
    # A down-gap just below price is supportive for longs but not for shorts.
    gaps = [_gap("down", 99.5, 99.95)]
    long_score = gap_confluence_score(
        price=100.0, gaps=gaps, atr_value=1.0, direction="long"
    )
    short_score = gap_confluence_score(
        price=100.0, gaps=gaps, atr_value=1.0, direction="short"
    )
    assert long_score > short_score
    assert short_score == 0.0


def test_score_bounded_zero_to_one():
    gaps = [_gap("down", 0.0, 100.0)]  # giant gap right under price
    s = gap_confluence_score(price=100.0, gaps=gaps, atr_value=1.0, direction="long")
    assert 0.0 <= s <= 1.0


def test_score_zero_when_atr_invalid():
    gaps = [_gap("down", 99.0, 99.9)]
    assert (
        gap_confluence_score(price=100.0, gaps=gaps, atr_value=0.0, direction="long")
        == 0.0
    )
