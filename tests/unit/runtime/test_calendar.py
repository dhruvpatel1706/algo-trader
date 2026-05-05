"""Unit tests for the market-hours calendar predicate.

No network or filesystem; everything runs against fixed timestamps. Where a
test wants "now" rather than an explicit ``ts``, we use freezegun so the
behavior is reproducible regardless of when the suite is run.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time
from src.runtime.calendar import ET, UTC, is_open, next_open, time_to_close


def _et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Helper: build a tz-aware ET datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=ET)


# --- Crypto: always open --------------------------------------------------


@pytest.mark.parametrize(
    "ts",
    [
        # Sunday 03:00 UTC
        datetime(2024, 6, 23, 3, 0, tzinfo=UTC),
        # Saturday afternoon
        datetime(2024, 6, 22, 18, 0, tzinfo=UTC),
        # Christmas Day
        datetime(2024, 12, 25, 14, 30, tzinfo=UTC),
        # Tuesday 04:00 UTC (overnight)
        datetime(2024, 6, 18, 4, 0, tzinfo=UTC),
        # New Year's Day midnight
        datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
    ],
)
def test_crypto_always_open(ts: datetime) -> None:
    assert is_open("crypto", ts) is True


def test_crypto_always_open_no_ts() -> None:
    # Even with the wall clock unspecified, crypto is open.
    assert is_open("crypto") is True


def test_crypto_time_to_close_is_none() -> None:
    assert time_to_close("crypto") is None
    assert time_to_close("crypto", datetime(2024, 6, 18, 14, 0, tzinfo=UTC)) is None


# --- Equity: regular session boundaries ----------------------------------


def test_equity_open_at_10_et_tuesday() -> None:
    # 2024-06-18 is a Tuesday, no holiday.
    assert is_open("equity", _et(2024, 6, 18, 10, 0)) is True


def test_equity_closed_at_09_et_tuesday() -> None:
    # 09:00 ET on a Tuesday is before the bell.
    assert is_open("equity", _et(2024, 6, 18, 9, 0)) is False


def test_equity_closed_at_1630_et_tuesday() -> None:
    # 16:30 ET is after the close.
    assert is_open("equity", _et(2024, 6, 18, 16, 30)) is False


def test_equity_closed_saturday_11_et() -> None:
    # 2024-06-22 is a Saturday.
    assert is_open("equity", _et(2024, 6, 22, 11, 0)) is False


def test_equity_closed_christmas_2024() -> None:
    # Dec 25 2024 — Wednesday holiday.
    assert is_open("equity", _et(2024, 12, 25, 12, 0)) is False


def test_gold_and_bonds_follow_nyse() -> None:
    # Same predicate as equity for the equity-like ETF asset classes.
    open_ts = _et(2024, 6, 18, 14, 0)
    closed_ts = _et(2024, 6, 22, 14, 0)
    assert is_open("gold", open_ts) is True
    assert is_open("bonds", open_ts) is True
    assert is_open("gold", closed_ts) is False
    assert is_open("bonds", closed_ts) is False


def test_naive_datetime_treated_as_utc() -> None:
    # 14:00 UTC == 10:00 ET on a Tuesday → market is open.
    naive = datetime(2024, 6, 18, 14, 0)
    assert is_open("equity", naive) is True


def test_unknown_asset_class_raises() -> None:
    with pytest.raises(ValueError, match="unknown asset_class"):
        is_open("forex", datetime(2024, 6, 18, 14, 0, tzinfo=UTC))  # type: ignore[arg-type]


# --- time_to_close --------------------------------------------------------


def test_time_to_close_positive_during_equity_hours() -> None:
    ts = _et(2024, 6, 18, 10, 0)  # 10:00 ET → 6h to close
    secs = time_to_close("equity", ts)
    assert secs is not None
    assert secs > 0
    # Sanity: ~6 hours = 21600s
    assert 21000 <= secs <= 22000


def test_time_to_close_zero_when_market_closed() -> None:
    closed = _et(2024, 6, 22, 11, 0)  # Saturday
    assert time_to_close("equity", closed) == 0.0


# --- next_open ------------------------------------------------------------


def test_next_open_returns_future_timestamp() -> None:
    ts = _et(2024, 6, 22, 11, 0)  # Saturday → next open is Mon 2024-06-24 09:30 ET
    nxt = next_open("equity", ts)
    assert nxt > ts.astimezone(UTC)


def test_next_open_before_bell_today() -> None:
    # Tuesday 08:00 ET → next open is the same day at 09:30 ET.
    ts = _et(2024, 6, 18, 8, 0)
    nxt = next_open("equity", ts)
    assert nxt.astimezone(ET).date() == ts.date()
    assert nxt.astimezone(ET).hour == 9
    assert nxt.astimezone(ET).minute == 30


def test_next_open_skips_holiday() -> None:
    # Dec 24 2024 (Tuesday, regular session in this list) 17:00 ET is after
    # close. Dec 25 is Christmas (closed). Next open is Dec 26 09:30 ET.
    ts = _et(2024, 12, 24, 17, 0)
    nxt = next_open("equity", ts).astimezone(ET)
    assert nxt.month == 12
    assert nxt.day == 26
    assert (nxt.hour, nxt.minute) == (9, 30)


def test_next_open_when_already_open_returns_now() -> None:
    ts = _et(2024, 6, 18, 10, 0)
    nxt = next_open("equity", ts)
    # Should be the same instant (in UTC).
    assert nxt == ts.astimezone(UTC)


def test_next_open_crypto_returns_input() -> None:
    ts = datetime(2024, 6, 22, 18, 0, tzinfo=UTC)
    assert next_open("crypto", ts) == ts


# --- freezegun: now-based default behavior --------------------------------


@freeze_time("2024-06-18 14:00:00", tz_offset=0)  # 14:00 UTC == 10:00 ET, Tuesday
def test_default_ts_uses_now_open() -> None:
    assert is_open("equity") is True
    secs = time_to_close("equity")
    assert secs is not None and secs > 0


@freeze_time("2024-06-22 14:00:00", tz_offset=0)  # Saturday
def test_default_ts_uses_now_closed() -> None:
    assert is_open("equity") is False
    assert time_to_close("equity") == 0.0


# --- consistency: a few additional specific moments ----------------------


def test_exactly_0930_et_is_open() -> None:
    # The bell rings at 09:30 — half-open interval [09:30, 16:00).
    assert is_open("equity", _et(2024, 6, 18, 9, 30)) is True


def test_exactly_1600_et_is_closed() -> None:
    # 16:00 itself is the close — not tradeable.
    assert is_open("equity", _et(2024, 6, 18, 16, 0)) is False


def test_other_timezone_input_handled() -> None:
    # 18:00 in London on a Tuesday during DST = 13:00 ET → open.
    london = ZoneInfo("Europe/London")
    ts = datetime(2024, 6, 18, 18, 0, tzinfo=london)
    assert is_open("equity", ts) is True
