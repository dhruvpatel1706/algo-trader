"""Unit tests for `src.risk.daily_loss` — DailyLossBreaker + JournalFileReader."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from src.risk.daily_loss import (
    DailyLossBreaker,
    DailyLossDecision,
    JournalFileReader,
    StaticEquityProvider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubReader:
    """In-memory JournalReader stub. The caller mutates ``pnl`` between checks."""

    def __init__(self, pnl: float = 0.0) -> None:
        self.pnl = pnl
        self.calls: int = 0

    def realized_pnl_today(self, today: date) -> float:
        self.calls += 1
        return self.pnl


def _clock_at(*moments: datetime) -> Callable[[], datetime]:
    """Return a callable that yields each moment in turn, then sticks at the last one."""
    iterator = iter(moments)
    last: list[datetime] = [moments[-1]]

    def _now() -> datetime:
        nonlocal last
        try:
            value = next(iterator)
            last[0] = value
            return value
        except StopIteration:
            return last[0]

    return _now


def _write_journal(path: Path, events: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [-0.21, -1.0, 0.01, 0.5, float("nan")])
def test_floor_pct_outside_range_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="floor_pct"):
        DailyLossBreaker(StaticEquityProvider(100_000.0), _StubReader(0.0), floor_pct=bad)


@pytest.mark.parametrize("good", [-0.20, -0.10, -0.03, 0.0])
def test_floor_pct_inside_range_allowed(good: float) -> None:
    # No exception.
    DailyLossBreaker(StaticEquityProvider(100_000.0), _StubReader(0.0), floor_pct=good)


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------


def test_zero_pnl_allows_new_opens() -> None:
    breaker = DailyLossBreaker(
        StaticEquityProvider(100_000.0), _StubReader(0.0), floor_pct=-0.03
    )
    decision = breaker.check()
    assert isinstance(decision, DailyLossDecision)
    assert decision.can_open_new is True
    assert decision.can_exit is True
    assert decision.realized_pnl_usd == 0.0
    assert decision.realized_pnl_pct == 0.0


def test_loss_above_floor_allows_new_opens() -> None:
    # -2% realized vs -3% floor → still OK.
    breaker = DailyLossBreaker(
        StaticEquityProvider(100_000.0), _StubReader(-2_000.0), floor_pct=-0.03
    )
    decision = breaker.check()
    assert decision.can_open_new is True
    assert decision.can_exit is True
    assert decision.realized_pnl_pct == pytest.approx(-0.02)


def test_loss_below_floor_blocks_new_opens() -> None:
    # -3.5% realized vs -3% floor → BLOCK new entries; exits still allowed.
    breaker = DailyLossBreaker(
        StaticEquityProvider(100_000.0), _StubReader(-3_500.0), floor_pct=-0.03
    )
    decision = breaker.check()
    assert decision.can_open_new is False
    assert decision.can_exit is True
    assert decision.realized_pnl_pct == pytest.approx(-0.035)


def test_exactly_at_floor_blocks_strict_inequality() -> None:
    # realized_pnl_pct == floor_pct → BLOCK (strict > comparison).
    breaker = DailyLossBreaker(
        StaticEquityProvider(100_000.0), _StubReader(-3_000.0), floor_pct=-0.03
    )
    decision = breaker.check()
    assert decision.can_open_new is False
    assert decision.can_exit is True


def test_zero_starting_equity_allows_new_opens_defensively() -> None:
    breaker = DailyLossBreaker(
        StaticEquityProvider(0.0), _StubReader(-1_000.0), floor_pct=-0.03
    )
    decision = breaker.check()
    # Cannot meaningfully compute a percent — fall through to allow.
    assert decision.can_open_new is True
    assert decision.realized_pnl_pct == 0.0


def test_negative_starting_equity_allows_new_opens_defensively() -> None:
    breaker = DailyLossBreaker(
        StaticEquityProvider(-500.0), _StubReader(-1_000.0), floor_pct=-0.03
    )
    decision = breaker.check()
    assert decision.can_open_new is True
    assert decision.realized_pnl_pct == 0.0


# ---------------------------------------------------------------------------
# Cache + clock behaviour
# ---------------------------------------------------------------------------


def test_check_within_cache_seconds_returns_same_decision() -> None:
    t0 = datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)
    reader = _StubReader(-2_000.0)
    breaker = DailyLossBreaker(
        StaticEquityProvider(100_000.0),
        reader,
        floor_pct=-0.03,
        clock=_clock_at(t0, t0 + timedelta(seconds=5), t0 + timedelta(seconds=29)),
        cache_seconds=30.0,
    )

    d1 = breaker.check()
    # Mutate the underlying source to prove the cached result wins.
    reader.pnl = -9_999.0
    d2 = breaker.check()
    d3 = breaker.check()

    assert d1 is d2 is d3
    # Reader called exactly once across three checks.
    assert reader.calls == 1


def test_check_after_cache_expiry_recomputes() -> None:
    t0 = datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)
    reader = _StubReader(-1_000.0)
    breaker = DailyLossBreaker(
        StaticEquityProvider(100_000.0),
        reader,
        floor_pct=-0.03,
        clock=_clock_at(t0, t0 + timedelta(seconds=31)),
        cache_seconds=30.0,
    )

    d1 = breaker.check()
    reader.pnl = -4_000.0
    d2 = breaker.check()

    assert d1 is not d2
    assert d1.can_open_new is True
    assert d2.can_open_new is False
    assert reader.calls == 2


def test_day_rollover_invalidates_cache_and_refetches_equity() -> None:
    """When UTC date changes, the breaker must reload starting equity for the new day."""
    t_day1 = datetime(2026, 5, 6, 23, 59, 50, tzinfo=UTC)
    t_day2 = datetime(2026, 5, 7, 0, 0, 5, tzinfo=UTC)  # only 15s later, but new date

    class _BumpingProvider:
        """Each call to ``starting_equity_today`` returns a different value."""

        def __init__(self) -> None:
            self.calls: int = 0
            self.values = [100_000.0, 95_000.0]

        def starting_equity_today(self, today: date) -> float:
            self.calls += 1
            return self.values[min(self.calls - 1, len(self.values) - 1)]

    provider = _BumpingProvider()
    reader = _StubReader(-500.0)
    breaker = DailyLossBreaker(
        provider,
        reader,
        floor_pct=-0.03,
        clock=_clock_at(t_day1, t_day2),
        cache_seconds=600.0,  # large so only the date rollover can invalidate
    )

    d1 = breaker.check()
    d2 = breaker.check()

    assert provider.calls == 2  # called once per day
    assert d1.starting_equity_usd == 100_000.0
    assert d2.starting_equity_usd == 95_000.0
    assert d1 is not d2


def test_reset_cache_forces_recompute() -> None:
    t0 = datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)
    reader = _StubReader(-1_000.0)
    breaker = DailyLossBreaker(
        StaticEquityProvider(100_000.0),
        reader,
        floor_pct=-0.03,
        clock=_clock_at(t0),
        cache_seconds=600.0,
    )
    breaker.check()
    breaker.reset_cache()
    reader.pnl = -4_000.0
    decision = breaker.check()
    assert decision.can_open_new is False


# ---------------------------------------------------------------------------
# Reason string formatting
# ---------------------------------------------------------------------------


def test_reason_contains_pct_and_floor_when_ok() -> None:
    breaker = DailyLossBreaker(
        StaticEquityProvider(100_000.0), _StubReader(-500.0), floor_pct=-0.03
    )
    decision = breaker.check()
    assert decision.can_open_new is True
    assert "OK" in decision.reason
    assert "-0.5%" in decision.reason
    assert "-3.0%" in decision.reason


def test_reason_contains_pct_and_floor_when_blocked() -> None:
    breaker = DailyLossBreaker(
        StaticEquityProvider(100_000.0), _StubReader(-3_200.0), floor_pct=-0.03
    )
    decision = breaker.check()
    assert decision.can_open_new is False
    assert "breached" in decision.reason.lower() or "halted" in decision.reason.lower()
    assert "-3.2%" in decision.reason
    assert "-3.0%" in decision.reason
    assert "exits still allowed" in decision.reason


# ---------------------------------------------------------------------------
# JournalFileReader
# ---------------------------------------------------------------------------


def test_journal_reader_missing_file_returns_zero(tmp_path: Path) -> None:
    reader = JournalFileReader(journal_dir=tmp_path)
    assert reader.realized_pnl_today(date(2026, 5, 6)) == 0.0


def test_journal_reader_sums_pnl_for_recognized_events(tmp_path: Path) -> None:
    today = date(2026, 5, 6)
    path = tmp_path / f"{today.isoformat()}.jsonl"
    _write_journal(
        path,
        [
            {"event": "exit", "pnl": -100.0, "ts": "2026-05-06T10:00:00Z"},
            {"event": "fill", "pnl": 50.5, "ts": "2026-05-06T11:00:00Z"},
            {"event": "trade_closed", "pnl": -25.25, "ts": "2026-05-06T12:00:00Z"},
        ],
    )
    reader = JournalFileReader(journal_dir=tmp_path)
    assert reader.realized_pnl_today(today) == pytest.approx(-74.75)


def test_journal_reader_skips_malformed_lines(tmp_path: Path) -> None:
    today = date(2026, 5, 6)
    path = tmp_path / f"{today.isoformat()}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write('{"event": "exit", "pnl": -10.0}\n')
        fh.write("not json at all\n")
        fh.write("\n")  # blank
        fh.write('{"event": "fill", "pnl": "not-a-number"}\n')
        fh.write('"top-level-string"\n')  # valid JSON but not a dict
        fh.write('{"event": "exit", "pnl": NaN}\n')  # invalid JSON literal
        fh.write('{"event": "exit", "pnl": -20.5}\n')

    reader = JournalFileReader(journal_dir=tmp_path)
    assert reader.realized_pnl_today(today) == pytest.approx(-30.5)


def test_journal_reader_only_sums_recognized_event_types(tmp_path: Path) -> None:
    today = date(2026, 5, 6)
    path = tmp_path / f"{today.isoformat()}.jsonl"
    _write_journal(
        path,
        [
            # Counted.
            {"event": "exit", "pnl": -100.0},
            {"event": "fill", "pnl": -50.0},
            {"event": "trade_closed", "pnl": 75.0},
            # NOT counted — different event types.
            {"event": "submit", "pnl": -1_000_000.0},
            {"event": "approve", "pnl": -1_000_000.0},
            {"gate": "risk", "pnl": -1_000_000.0},  # no "event" key at all
            # NOT counted — non-finite.
            {"event": "exit", "pnl": float("inf")},
        ],
    )
    reader = JournalFileReader(journal_dir=tmp_path)
    assert reader.realized_pnl_today(today) == pytest.approx(-75.0)


def test_journal_reader_handles_str_pnl_defensively(tmp_path: Path) -> None:
    """String pnl values are skipped (not converted)."""
    today = date(2026, 5, 6)
    path = tmp_path / f"{today.isoformat()}.jsonl"
    _write_journal(
        path,
        [
            {"event": "exit", "pnl": "-100.0"},  # string, ignored
            {"event": "exit", "pnl": -50.0},  # counted
        ],
    )
    reader = JournalFileReader(journal_dir=tmp_path)
    assert reader.realized_pnl_today(today) == pytest.approx(-50.0)
