"""Integration of `check_daily_loss` (in `src.risk.limits`) with `DailyLossBreaker`."""

from __future__ import annotations

from datetime import date

from src.risk.daily_loss import DailyLossBreaker, StaticEquityProvider
from src.risk.limits import check_daily_loss


class _StubReader:
    def __init__(self, pnl: float) -> None:
        self._pnl = pnl

    def realized_pnl_today(self, today: date) -> float:
        return self._pnl


def _make_breaker(realized_pnl: float, floor_pct: float = -0.03) -> DailyLossBreaker:
    return DailyLossBreaker(
        StaticEquityProvider(100_000.0),
        _StubReader(realized_pnl),
        floor_pct=floor_pct,
    )


def test_check_daily_loss_returns_allowed_when_not_breached() -> None:
    # -1% loss, -3% floor → allowed.
    breaker = _make_breaker(realized_pnl=-1_000.0, floor_pct=-0.03)
    allowed, reason = check_daily_loss(breaker)
    assert allowed is True
    assert isinstance(reason, str) and reason


def test_check_daily_loss_returns_blocked_when_breached() -> None:
    # -3.5% loss, -3% floor → blocked.
    breaker = _make_breaker(realized_pnl=-3_500.0, floor_pct=-0.03)
    allowed, reason = check_daily_loss(breaker)
    assert allowed is False
    assert isinstance(reason, str) and reason


def test_check_daily_loss_reason_strings_are_non_empty_in_both_cases() -> None:
    ok_breaker = _make_breaker(realized_pnl=0.0, floor_pct=-0.03)
    blocked_breaker = _make_breaker(realized_pnl=-5_000.0, floor_pct=-0.03)
    _, ok_reason = check_daily_loss(ok_breaker)
    _, blocked_reason = check_daily_loss(blocked_breaker)
    assert ok_reason.strip() != ""
    assert blocked_reason.strip() != ""
    # Sanity: the two reason strings are clearly different.
    assert ok_reason != blocked_reason
