"""Market-hours predicate for the multi-asset 24/7 runner.

Tells the scheduler whether a given asset class is currently tradeable.
Equities (and equity-like ETFs covering gold/bonds) follow the NYSE regular
session (09:30-16:00 ET, Mon-Fri, excluding US market holidays). Crypto is
always open.

Pure functions, no global state, no I/O, no logging. If
``pandas_market_calendars`` is importable we use it; otherwise we fall back
to a hardcoded weekday + holiday table that's good enough for v1
(2024-2026).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

AssetClass = Literal["equity", "gold", "bonds", "crypto"]

_NYSE_LIKE: frozenset[str] = frozenset({"equity", "gold", "silver", "bonds"})

# NYSE regular hours (no early-close handling in v1; half-days are still
# considered open until 16:00 ET, which biases conservative-late rather than
# missing sessions entirely).
_OPEN_TIME = time(9, 30)
_CLOSE_TIME = time(16, 0)

# Hardcoded NYSE holidays for 2024-2026. Sufficient for v1; replace with
# ``pandas_market_calendars`` once available in the env.
_NYSE_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2024
        date(2024, 1, 1),  # New Year's Day
        date(2024, 1, 15),  # MLK Day
        date(2024, 2, 19),  # Presidents' Day
        date(2024, 3, 29),  # Good Friday
        date(2024, 5, 27),  # Memorial Day
        date(2024, 6, 19),  # Juneteenth
        date(2024, 7, 4),  # Independence Day
        date(2024, 9, 2),  # Labor Day
        date(2024, 11, 28),  # Thanksgiving
        date(2024, 12, 25),  # Christmas
        # 2025
        date(2025, 1, 1),
        date(2025, 1, 9),  # Day of mourning (Carter) — observed close
        date(2025, 1, 20),  # MLK Day
        date(2025, 2, 17),  # Presidents' Day
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 26),  # Memorial Day
        date(2025, 6, 19),  # Juneteenth
        date(2025, 7, 4),  # Independence Day
        date(2025, 9, 1),  # Labor Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas
        # 2026
        date(2026, 1, 1),
        date(2026, 1, 19),  # MLK Day
        date(2026, 2, 16),  # Presidents' Day
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth (observed)
        date(2026, 7, 3),  # Independence Day (observed; Jul 4 is Saturday)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    }
)


def _try_mcal_nyse():
    """Return a pandas_market_calendars NYSE calendar, or None if unavailable."""
    try:
        import pandas_market_calendars as mcal  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return None
    return mcal.get_calendar("NYSE")


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _coerce(ts: datetime | None) -> datetime:
    """Return a tz-aware UTC datetime. Naive inputs are interpreted as UTC."""
    if ts is None:
        return _now_utc()
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


def _is_nyse_session_day(d: date) -> bool:
    """True if ``d`` is a regular NYSE trading day (weekday & not a holiday)."""
    cal = _try_mcal_nyse()
    if cal is not None:
        # ``valid_days`` returns a DatetimeIndex of session dates in [start, end].
        sched = cal.valid_days(start_date=d.isoformat(), end_date=d.isoformat())
        return len(sched) > 0
    # Fallback: weekday + hardcoded holiday table.
    if d.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
    return d not in _NYSE_HOLIDAYS


def _equity_open_at(ts_utc: datetime) -> bool:
    et = ts_utc.astimezone(ET)
    if not _is_nyse_session_day(et.date()):
        return False
    return _OPEN_TIME <= et.time() < _CLOSE_TIME


def is_open(asset_class: AssetClass, ts: datetime | None = None) -> bool:
    """Whether the asset class is currently tradeable.

    - ``equity`` / ``gold`` / ``bonds``: NYSE regular session (09:30-16:00 ET,
      Mon-Fri, excluding US market holidays).
    - ``crypto``: always True.

    ``ts`` defaults to ``datetime.now(UTC)``. Naive datetimes are interpreted
    as UTC.
    """
    if asset_class == "crypto":
        return True
    if asset_class in _NYSE_LIKE:
        return _equity_open_at(_coerce(ts))
    raise ValueError(f"unknown asset_class: {asset_class!r}")


def next_open(asset_class: str, ts: datetime | None = None) -> datetime:
    """Timestamp (UTC) of the next time ``is_open(asset_class)`` becomes True.

    For crypto, returns ``ts`` itself (always open).
    """
    if asset_class == "crypto":
        return _coerce(ts)
    if asset_class not in _NYSE_LIKE:
        raise ValueError(f"unknown asset_class: {asset_class!r}")

    now_utc = _coerce(ts)
    if _equity_open_at(now_utc):
        return now_utc

    # Walk forward day-by-day in ET. The first session day whose 09:30 ET is
    # strictly after ``now_utc`` is the answer.
    et = now_utc.astimezone(ET)
    candidate_date = et.date()
    # If we're past today's close (or today isn't a session day), start with
    # tomorrow; otherwise the bell hasn't rung yet today.
    today_open_et = datetime.combine(candidate_date, _OPEN_TIME, tzinfo=ET)
    if _is_nyse_session_day(candidate_date) and et < today_open_et:
        return today_open_et.astimezone(UTC)

    candidate_date = candidate_date + timedelta(days=1)
    # Bound the loop. NYSE never has more than ~5-6 consecutive non-session
    # days; 30 is a safe upper bound that also handles holes in our
    # hardcoded holiday table without spinning forever.
    for _ in range(30):
        if _is_nyse_session_day(candidate_date):
            return datetime.combine(candidate_date, _OPEN_TIME, tzinfo=ET).astimezone(UTC)
        candidate_date += timedelta(days=1)
    raise RuntimeError("no NYSE session found in the next 30 days — holiday table stale?")


def time_to_close(asset_class: str, ts: datetime | None = None) -> float | None:
    """Seconds until the current session closes.

    Returns ``None`` for asset classes without a fixed close (``crypto``).
    Returns ``0.0`` when the market isn't currently open (caller should pair
    this with ``is_open``; an explicit zero is friendlier than negative
    seconds).
    """
    if asset_class == "crypto":
        return None
    if asset_class not in _NYSE_LIKE:
        raise ValueError(f"unknown asset_class: {asset_class!r}")

    now_utc = _coerce(ts)
    if not _equity_open_at(now_utc):
        return 0.0
    et = now_utc.astimezone(ET)
    close_et = datetime.combine(et.date(), _CLOSE_TIME, tzinfo=ET)
    return (close_et - et).total_seconds()
