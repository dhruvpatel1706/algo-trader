"""Tests for src.runtime.market_phase.

Covers:
- Equity phase boundary precision (right-open intervals).
- Weekend / holiday handling for equity vs crypto.
- DST-aware UTC->NY conversion (EST and EDT both exercised).
- Naive datetime rejection.
- ``is_market_open`` consistency across phases.
- ``phase_posture`` returns a non-empty string for every phase.
- FX weekend window.
- Edge: midnight UTC during US market hours.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from src.runtime.market_phase import (
    MarketPhase,
    current_phase,
    is_market_open,
    phase_posture,
)

NY = ZoneInfo("America/New_York")

# A confirmed weekday in EST winter (no DST in effect).
EST_DATE = (2025, 1, 15)  # Wednesday, 2025-01-15 — EST
# A confirmed weekday in EDT summer (DST in effect).
EDT_DATE = (2025, 7, 15)  # Tuesday, 2025-07-15 — EDT


def _ny(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=NY)


# ---------------------------------------------------------------------------
# 1. Phase boundary precision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "minute", "second", "expected"),
    [
        (4, 0, 0, "pre_market"),
        (9, 29, 59, "pre_market"),
        (9, 30, 0, "open"),
        (9, 59, 59, "open"),
        (10, 0, 0, "midday"),
        (12, 0, 0, "midday"),
        (14, 59, 59, "midday"),
        (15, 0, 0, "close"),
        (15, 59, 59, "close"),
        (16, 0, 0, "after_hours"),
        (19, 59, 59, "after_hours"),
        (20, 0, 0, "closed"),
        (3, 59, 59, "closed"),
        (0, 0, 0, "closed"),
    ],
)
def test_equity_phase_boundary_precision(
    hour: int, minute: int, second: int, expected: MarketPhase
) -> None:
    # Use the confirmed-weekday EST date so DST doesn't perturb the wall clock.
    ts = _ny(*EST_DATE, hour, minute, second)
    assert current_phase(ts, "equity") == expected


# ---------------------------------------------------------------------------
# 2. Weekend equity -> closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "month", "day"),
    [
        (2025, 1, 18),  # Saturday
        (2025, 1, 19),  # Sunday
        (2025, 7, 12),  # Saturday (EDT)
        (2025, 7, 13),  # Sunday (EDT)
    ],
)
@pytest.mark.parametrize("hour", [0, 9, 10, 12, 16, 23])
def test_weekend_equity_is_closed(year: int, month: int, day: int, hour: int) -> None:
    ts = _ny(year, month, day, hour)
    assert current_phase(ts, "equity") == "closed"


# ---------------------------------------------------------------------------
# 3. Weekend crypto -> open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "month", "day", "hour"),
    [
        (2025, 1, 18, 0),
        (2025, 1, 18, 12),
        (2025, 1, 19, 23),
        (2025, 7, 12, 6),
    ],
)
def test_weekend_crypto_is_open(year: int, month: int, day: int, hour: int) -> None:
    ts = _ny(year, month, day, hour)
    assert current_phase(ts, "crypto") == "open"


# ---------------------------------------------------------------------------
# 4. Holiday equity -> closed
# ---------------------------------------------------------------------------


def test_independence_day_2025_equity_closed() -> None:
    # 2025-07-04 is a Friday holiday — equity must be closed all day.
    ts = _ny(2025, 7, 4, 11, 0)
    assert current_phase(ts, "equity") == "closed"


def test_thanksgiving_2025_equity_closed() -> None:
    ts = _ny(2025, 11, 27, 11, 0)
    assert current_phase(ts, "equity") == "closed"


def test_independence_day_observed_2026_equity_closed() -> None:
    # July 4, 2026 is Saturday; observed holiday is Friday Jul 3.
    ts = _ny(2026, 7, 3, 11, 0)
    assert current_phase(ts, "equity") == "closed"


# ---------------------------------------------------------------------------
# 5. Holiday crypto -> open
# ---------------------------------------------------------------------------


def test_independence_day_2025_crypto_open() -> None:
    ts = _ny(2025, 7, 4, 11, 0)
    assert current_phase(ts, "crypto") == "open"


# ---------------------------------------------------------------------------
# 6. DST-aware UTC -> NY conversion
# ---------------------------------------------------------------------------


def test_utc_input_is_converted_est_winter() -> None:
    # 2025-01-15 (winter, EST = UTC-5). 09:30 ET = 14:30 UTC.
    ts_utc = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
    assert current_phase(ts_utc, "equity") == "open"

    # 13:30 UTC on the same day is 08:30 ET -> pre_market.
    ts_pre = datetime(2025, 1, 15, 13, 30, tzinfo=UTC)
    assert current_phase(ts_pre, "equity") == "pre_market"


def test_utc_input_is_converted_edt_summer() -> None:
    # 2025-07-15 (summer, EDT = UTC-4). 09:30 ET = 13:30 UTC.
    ts_utc = datetime(2025, 7, 15, 13, 30, tzinfo=UTC)
    assert current_phase(ts_utc, "equity") == "open"

    # 14:30 UTC same day = 10:30 ET -> midday.
    ts_mid = datetime(2025, 7, 15, 14, 30, tzinfo=UTC)
    assert current_phase(ts_mid, "equity") == "midday"


def test_non_ny_tz_input_normalizes_correctly() -> None:
    # 2025-01-15 09:30 ET == 15:30 in Europe/Berlin (CET = UTC+1).
    berlin = ZoneInfo("Europe/Berlin")
    ts = datetime(2025, 1, 15, 15, 30, tzinfo=berlin)
    assert current_phase(ts, "equity") == "open"


# ---------------------------------------------------------------------------
# 7. Naive datetime raises ValueError
# ---------------------------------------------------------------------------


def test_naive_datetime_raises() -> None:
    naive = datetime(2025, 1, 15, 14, 30)
    with pytest.raises(ValueError, match="timezone-aware"):
        current_phase(naive, "equity")


def test_naive_datetime_raises_for_is_market_open() -> None:
    naive = datetime(2025, 1, 15, 14, 30)
    with pytest.raises(ValueError, match="timezone-aware"):
        is_market_open(naive, "equity")


# ---------------------------------------------------------------------------
# 8. is_market_open consistency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "minute", "expected_open"),
    [
        (4, 0, False),  # pre_market
        (9, 29, False),  # pre_market
        (9, 30, True),  # open
        (12, 0, True),  # midday
        (15, 30, True),  # close
        (16, 0, False),  # after_hours
        (20, 0, False),  # closed
    ],
)
def test_is_market_open_matches_phase_classification(
    hour: int, minute: int, expected_open: bool
) -> None:
    ts = _ny(*EST_DATE, hour, minute)
    assert is_market_open(ts, "equity") is expected_open


def test_is_market_open_crypto_is_always_true() -> None:
    # Crypto: any time, including weekends and holidays.
    ts_weekend = _ny(2025, 1, 18, 3, 0)
    ts_holiday = _ny(2025, 7, 4, 11, 0)
    assert is_market_open(ts_weekend, "crypto") is True
    assert is_market_open(ts_holiday, "crypto") is True


def test_is_market_open_weekend_equity_is_false() -> None:
    ts = _ny(2025, 1, 18, 12)
    assert is_market_open(ts, "equity") is False


# ---------------------------------------------------------------------------
# 9. phase_posture returns non-empty string for every phase
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phase",
    ["pre_market", "open", "midday", "close", "after_hours", "closed"],
)
def test_phase_posture_non_empty(phase: MarketPhase) -> None:
    p = phase_posture(phase)
    assert isinstance(p, str)
    assert p.strip() != ""
    # Pin the leading token so callers can rely on the prefix.
    assert len(p) > 20


def test_phase_posture_distinct_per_phase() -> None:
    phases: list[MarketPhase] = [
        "pre_market",
        "open",
        "midday",
        "close",
        "after_hours",
        "closed",
    ]
    postures = {phase_posture(p) for p in phases}
    assert len(postures) == len(phases)


# ---------------------------------------------------------------------------
# 10. FX weekend handling
# ---------------------------------------------------------------------------


def test_fx_weekday_is_open() -> None:
    ts = _ny(*EST_DATE, 3, 0)  # 03:00 ET on a Wednesday — equity closed, FX open.
    assert current_phase(ts, "fx") == "open"
    assert is_market_open(ts, "fx") is True


def test_fx_saturday_is_closed() -> None:
    # 2025-01-18 is Saturday in NY local.
    for hour in (0, 6, 12, 18, 23):
        ts = _ny(2025, 1, 18, hour)
        assert current_phase(ts, "fx") == "closed"
        assert is_market_open(ts, "fx") is False


def test_fx_sunday_is_open() -> None:
    # v1 simplification: full Sunday treated as open.
    ts = _ny(2025, 1, 19, 12)
    assert current_phase(ts, "fx") == "open"


# ---------------------------------------------------------------------------
# 11. Edge: midnight UTC during US market hours
# ---------------------------------------------------------------------------


def test_midnight_utc_during_us_market_hours() -> None:
    # 00:00 UTC on a US trading day -> 19:00 (EST) or 20:00 (EDT) the
    # PREVIOUS calendar day in NY. Both fall within after_hours/closed,
    # never within the regular session.
    # Pick the day AFTER our EST date so the conversion lands on EST_DATE.
    ts_after_est = datetime(2025, 1, 16, 0, 0, tzinfo=UTC)
    # 00:00 UTC on 2025-01-16 == 19:00 ET on 2025-01-15 (Wednesday) -> after_hours.
    assert current_phase(ts_after_est, "equity") == "after_hours"

    ts_after_edt = datetime(2025, 7, 16, 0, 0, tzinfo=UTC)
    # 00:00 UTC on 2025-07-16 == 20:00 ET on 2025-07-15 (Tuesday) -> closed.
    assert current_phase(ts_after_edt, "equity") == "closed"


# ---------------------------------------------------------------------------
# Extra: NYSE-aligned asset classes follow equity schedule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("asset_class", ["gold", "silver", "bonds", "options"])
def test_nyse_aligned_classes_match_equity(asset_class: str) -> None:
    ts = _ny(*EST_DATE, 12, 0)
    # Cast so mypy/ruff-N are happy; current_phase accepts the Literal already.
    assert current_phase(ts, asset_class) == current_phase(ts, "equity")  # type: ignore[arg-type]


def test_holiday_for_nyse_aligned_class_is_closed() -> None:
    ts = _ny(2025, 12, 25, 11, 0)
    for asset_class in ("equity", "gold", "silver", "bonds", "options"):
        assert current_phase(ts, asset_class) == "closed"  # type: ignore[arg-type]
