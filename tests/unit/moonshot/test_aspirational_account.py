from __future__ import annotations

import inspect
import math
from datetime import UTC, datetime

import pytest
from src.moonshot import aspirational_account
from src.moonshot.aspirational_account import (
    AspirationalAccount,
    project_forward,
    required_daily_return,
)


def test_progress_fraction_log_scaled() -> None:
    acct = AspirationalAccount()
    # At start, progress is 0.
    assert acct.progress_fraction == 0.0

    # Halfway in log space: sqrt(starting * target) -> 0.5.
    midpoint = math.sqrt(acct.starting_equity * acct.target_equity)
    acct.update_equity(midpoint)
    assert acct.progress_fraction == pytest.approx(0.5)

    # At target the fraction is exactly 1.0.
    acct.update_equity(acct.target_equity)
    assert acct.progress_fraction == 1.0

    # Beyond target it caps at 1.0.
    acct.update_equity(acct.target_equity * 10)
    assert acct.progress_fraction == 1.0

    # Linear-scaled would put 1k as 0.000045 of 2M; log-scaled puts it at ~0.235.
    acct.update_equity(1_000.0)
    assert 0.20 < acct.progress_fraction < 0.30


def test_required_daily_return_100_to_2m_252d_around_4pct() -> None:
    """$100 -> $2,000,000 in 252 trading days needs ~4.0% per day. Reality check."""
    r = required_daily_return(100.0, 2_000_000.0, 252)
    # 252 trading days. Closed-form: (2e6/1e2)^(1/252) - 1 ≈ 0.04004.
    assert 0.038 < r < 0.042


def test_required_daily_return_edges() -> None:
    assert required_daily_return(100.0, 100.0, 252) == 0.0
    # No days -> infeasible.
    assert math.isinf(required_daily_return(100.0, 200.0, 0))


def test_project_forward_round_trip() -> None:
    starting = 100.0
    days = 252
    r = required_daily_return(starting, 2_000_000.0, days)
    final = project_forward(starting, r, days)
    assert math.isclose(final, 2_000_000.0, rel_tol=1e-6)


def test_days_at_required_compounding_zero_when_unstarted() -> None:
    acct = AspirationalAccount()
    assert acct.days_at_required_compounding == 0


def test_days_at_required_compounding_naive_datetime_supported() -> None:
    # Use a long-ago naive datetime to verify tz coercion.
    acct = AspirationalAccount(started_at=datetime(2020, 1, 1))
    assert acct.days_at_required_compounding > 0


def test_paper_only_safety_flag() -> None:
    assert AspirationalAccount.LIVE_BROKER_BRIDGE is False
    src = inspect.getsource(aspirational_account)
    for token in ["src.execution", "alpaca", "TradingClient"]:
        assert token not in src

    # tz-aware datetime path also covered.
    acct = AspirationalAccount(started_at=datetime(2020, 1, 1, tzinfo=UTC))
    assert acct.days_at_required_compounding > 0
