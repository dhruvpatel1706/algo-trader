"""Gap detection and price-level utilities.

Power-of-Stocks-derived: unfilled price gaps act as support/resistance. These
helpers are intended to be used as a CONFLUENCE multiplier on existing strategy
signals, never as a standalone trade trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


@dataclass(frozen=True, slots=True)
class Gap:
    ts: pd.Timestamp
    direction: Literal["up", "down"]  # up gap = today's low > yesterday's high
    upper: float  # top of the gap
    lower: float  # bottom of the gap
    age_bars: int  # bars since gap occurred
    filled: bool  # has any subsequent bar's price re-entered the gap?


# Distance threshold (in ATR units) beyond which a gap is treated as
# "irrelevant" for confluence scoring.
_MAX_RELEVANT_DIST_ATR = 20.0
# Within this many ATR, a gap edge is considered close enough to score.
_CLOSE_ATR = 0.5


def _gap_range(prev_high: float, prev_low: float, cur_high: float, cur_low: float) -> tuple[
    Literal["up", "down"] | None, float, float
]:
    """Return (direction, upper, lower) for a gap between two adjacent bars,
    or (None, nan, nan) if there is no gap.

    Up gap:  cur_low > prev_high  -> gap is [prev_high, cur_low]
    Down gap: cur_high < prev_low -> gap is [cur_high, prev_low]
    """
    if cur_low > prev_high:
        return "up", cur_low, prev_high
    if cur_high < prev_low:
        return "down", prev_low, cur_high
    return None, float("nan"), float("nan")


def unfilled_gaps(
    bars: pd.DataFrame,
    lookback: int = 252,
    min_gap_pct: float = 0.005,
) -> list[Gap]:
    """Return all unfilled gaps in the last ``lookback`` bars, sorted oldest -> newest.

    ``bars`` must have ``high`` and ``low`` columns and a sorted DatetimeIndex.
    A gap is detected between adjacent bars; ``min_gap_pct`` is measured against
    the prior bar's reference edge (its high for up gaps, its low for down gaps)
    to filter out trivial gaps. A gap is considered FILLED if any subsequent
    bar's [low, high] range overlaps the gap's [lower, upper] range.
    """
    if bars is None or len(bars) < 2:
        return []

    # Use only the tail we care about — gap detection looks at adjacent pairs,
    # so include one extra bar of context to anchor the first comparison.
    window = bars.tail(lookback + 1) if lookback > 0 else bars
    if len(window) < 2:
        return []

    highs = window["high"].to_numpy(dtype=float)
    lows = window["low"].to_numpy(dtype=float)
    index = window.index
    n = len(window)

    gaps: list[Gap] = []
    for i in range(1, n):
        prev_high = highs[i - 1]
        prev_low = lows[i - 1]
        cur_high = highs[i]
        cur_low = lows[i]

        direction, upper, lower = _gap_range(prev_high, prev_low, cur_high, cur_low)
        if direction is None:
            continue

        # Min-gap filter: percentage of prior reference price.
        if direction == "up":
            ref = prev_high
            gap_size = cur_low - prev_high
        else:
            ref = prev_low
            gap_size = prev_low - cur_high
        if ref <= 0 or gap_size / ref < min_gap_pct:
            continue

        # Has any later bar re-entered the gap?
        filled = False
        if i + 1 < n:
            later_high = highs[i + 1 :]
            later_low = lows[i + 1 :]
            # Overlap with [lower, upper] iff later_low <= upper AND later_high >= lower.
            overlap = (later_low <= upper) & (later_high >= lower)
            filled = bool(overlap.any())

        if filled:
            continue

        gaps.append(
            Gap(
                ts=index[i],
                direction=direction,
                upper=float(upper),
                lower=float(lower),
                age_bars=int(n - 1 - i),
                filled=False,
            )
        )

    return gaps


def _nearest_edge_signed_distance(price: float, gaps: list[Gap]) -> float | None:
    """Signed price distance to the nearest gap edge (negative = below price,
    positive = above). Returns None if ``gaps`` is empty.

    For a gap entirely below price, the relevant edge is its ``upper`` (top);
    for a gap entirely above price, the relevant edge is its ``lower`` (bottom).
    For a gap straddling price, distance is 0.
    """
    if not gaps:
        return None

    best_signed: float | None = None
    best_abs = float("inf")
    for g in gaps:
        if price >= g.upper:
            signed = -(price - g.upper)  # gap below price -> negative
        elif price <= g.lower:
            signed = g.lower - price  # gap above price -> positive
        else:
            signed = 0.0  # price is inside the gap range

        magnitude = abs(signed)
        if magnitude < best_abs:
            best_abs = magnitude
            best_signed = signed

    return best_signed


def nearest_gap_distance_atr(
    price: float,
    gaps: list[Gap],
    atr_value: float,
) -> float | None:
    """Distance from ``price`` to the nearest unfilled gap edge, in ATR units.

    Negative if the gap is below price, positive if above. ``None`` if no gap
    is within 20 ATR (treated as no relevant level) or if ``atr_value`` is not
    a usable positive number.
    """
    if not gaps or atr_value is None or atr_value <= 0:
        return None

    signed = _nearest_edge_signed_distance(price, gaps)
    if signed is None:
        return None

    dist_atr = signed / atr_value
    if abs(dist_atr) > _MAX_RELEVANT_DIST_ATR:
        return None
    return dist_atr


def gap_confluence_score(
    price: float,
    gaps: list[Gap],
    atr_value: float,
    direction: Literal["long", "short"],
) -> float:
    """Score in [0, 1] indicating how much a gap edge supports the trade direction.

    For ``long``: the score increases when an unfilled DOWN gap (which acts as
    support from above when price has cleared it, i.e. its upper edge sits just
    below current price) is within ``_CLOSE_ATR`` ATR below the price.

    For ``short``: the score increases when an unfilled UP gap (resistance) is
    within ``_CLOSE_ATR`` ATR above the price.

    Used as a multiplier: ``signal.confidence *= 1 + 0.3 * score``.
    """
    if not gaps or atr_value is None or atr_value <= 0:
        return 0.0

    target_direction: Literal["up", "down"] = "down" if direction == "long" else "up"

    best = 0.0
    for g in gaps:
        if g.direction != target_direction:
            continue

        if direction == "long":
            # Long wants a down-gap acting as support BELOW price: its upper
            # edge should sit at or below price, and the distance from price
            # down to that edge should be within _CLOSE_ATR.
            if g.upper > price:
                continue
            dist_atr = (price - g.upper) / atr_value
        else:
            # Short wants an up-gap acting as resistance ABOVE price: its lower
            # edge should sit at or above price, distance up to it within _CLOSE_ATR.
            if g.lower < price:
                continue
            dist_atr = (g.lower - price) / atr_value

        if dist_atr < 0 or dist_atr > _CLOSE_ATR:
            continue

        # Linear falloff: dist 0 -> 1.0, dist _CLOSE_ATR -> 0.0
        score = 1.0 - (dist_atr / _CLOSE_ATR)
        best = max(best, score)

    # Clamp for safety.
    if best < 0.0:
        return 0.0
    if best > 1.0:
        return 1.0
    return best
