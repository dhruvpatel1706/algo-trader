"""Aspirational lane: $100 -> $2M paper account.

A separate paper-account configuration. All moonshot strategies enabled; standard
risk caps still apply (you cannot blow this account up faster than 1% per trade
per src/config.py). Tracks compounding rate as a north-star metric — NOT an
acceptance metric for any live-capital decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import ClassVar


@dataclass(slots=True)
class AspirationalAccount:
    """A separate paper-account configuration.

    Initial: $100. North-star: $2,000,000.
    All moonshot strategies enabled; standard risk caps still apply (you cannot
    blow this account up faster than 1% per trade per src/config.py).

    Purpose: track compounding rate as a north-star metric. NOT an acceptance
    metric for any live-capital decision — just a motivator.
    """

    # Lane is paper-only. Asserted by tests; do not flip. ClassVar so it doesn't
    # consume a slot.
    LIVE_BROKER_BRIDGE: ClassVar[bool] = False

    starting_equity: float = 100.0
    target_equity: float = 2_000_000.0
    current_equity: float = 100.0
    peak_equity: float = 100.0
    started_at: datetime | None = None

    @property
    def progress_fraction(self) -> float:
        """0..1: fraction of target reached (log-scaled — geometric mean).

        Log-scaled because compound growth is multiplicative — going $100 -> $1000
        is the same "fraction of effort" as $1000 -> $10000 etc.
        """
        if self.current_equity <= 0:
            return 0.0
        if self.current_equity >= self.target_equity:
            return 1.0
        if self.starting_equity <= 0 or self.target_equity <= self.starting_equity:
            return 0.0
        num = math.log(self.current_equity / self.starting_equity)
        den = math.log(self.target_equity / self.starting_equity)
        if den <= 0:
            return 0.0
        return max(0.0, num / den)

    @property
    def days_at_required_compounding(self) -> int:
        """Days from start where compounding rate is on track.

        Useful diagnostic. If 0 the lane never started or the account has not
        been updated yet.
        """
        if self.started_at is None:
            return 0
        now = datetime.now(UTC)
        started = self.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        delta = now - started
        return max(0, delta.days)

    def update_equity(self, new_equity: float) -> None:
        """Record a new equity reading. Tracks peak."""
        self.current_equity = float(new_equity)
        if new_equity > self.peak_equity:
            self.peak_equity = float(new_equity)


def required_daily_return(starting: float, target: float, days: int) -> float:
    """E.g. $100 -> $2M in 252 days requires ~4%/day. Reality check.

    Returns the daily return as a fraction (0.04 = 4%/day).
    """
    if starting <= 0 or target <= 0 or days <= 0:
        return float("inf")
    if target <= starting:
        return 0.0
    return (target / starting) ** (1.0 / days) - 1.0


def project_forward(starting: float, daily_return: float, days: int) -> float:
    """Compound `starting` at `daily_return` for `days`. Pure helper for tests/UI."""
    if days < 0:
        return starting
    return starting * (1.0 + daily_return) ** days


__all__ = [
    "AspirationalAccount",
    "project_forward",
    "required_daily_return",
]


# Re-export of date for users that pass typed args via this module.
_ = date  # silence lint
