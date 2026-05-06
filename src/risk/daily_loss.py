"""Daily-loss circuit breaker.

A pre-trade gate that halts NEW entries once the cumulative realized P&L for the
current UTC day breaches a configured floor (default -3% of starting equity).
Exits are always allowed.

This module is intentionally additive — it does not modify the existing
per-trade or portfolio-heat caps in :mod:`src.risk.limits`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

# Journal events that represent realized P&L on a closed trade. The repo has
# historically used several names; we accept any of them defensively so the
# reader works across journal-format revisions.
_PNL_EVENTS: frozenset[str] = frozenset({"exit", "fill", "trade_closed"})

# Allowed range for the floor: between -20% and 0% (a positive floor would never
# block, a stricter-than-20% floor would be a kill-switch, not a daily cap).
_FLOOR_MIN: float = -0.20
_FLOOR_MAX: float = 0.0


@dataclass(slots=True, frozen=True)
class DailyLossDecision:
    """Result of a single :meth:`DailyLossBreaker.check` call."""

    can_open_new: bool
    can_exit: bool
    realized_pnl_usd: float
    realized_pnl_pct: float
    floor_pct: float
    starting_equity_usd: float
    reason: str
    asof: str


class EquityProvider(Protocol):
    """Returns the equity at the start of the UTC day for `today`."""

    def starting_equity_today(self, today: date) -> float: ...


class JournalReader(Protocol):
    """Sums realized P&L from today's journal file (UTC date)."""

    def realized_pnl_today(self, today: date) -> float: ...


class JournalFileReader:
    """Reads a daily JSONL journal at ``<journal_dir>/<UTC_date>.jsonl``.

    Sums the ``pnl`` field on events whose ``event`` key is one of
    ``{"exit", "fill", "trade_closed"}``. Defensive against missing files,
    malformed lines, and non-finite numbers — any error returns ``0.0``.
    """

    def __init__(self, journal_dir: Path | str = Path("live/journal")) -> None:
        self._dir = Path(journal_dir)

    def realized_pnl_today(self, today: date) -> float:
        """Return today's realized P&L in USD (sum of ``pnl`` on closing events)."""
        path = self._dir / f"{today.isoformat()}.jsonl"
        if not path.exists():
            return 0.0

        total = 0.0
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        # Malformed line — skip silently.
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("event") not in _PNL_EVENTS:
                        continue
                    pnl = event.get("pnl")
                    if not isinstance(pnl, (int, float)):
                        continue
                    pnl_f = float(pnl)
                    if not math.isfinite(pnl_f):
                        continue
                    total += pnl_f
        except OSError:
            # File disappeared mid-read or permission flipped; never crash the breaker.
            return 0.0

        return total


class StaticEquityProvider:
    """Stub :class:`EquityProvider` for v1.

    Live integration uses :mod:`src.execution.broker`. Tests and offline tools
    pass a fixed dollar value via this provider.
    """

    def __init__(self, starting_equity_usd: float) -> None:
        self._eq = float(starting_equity_usd)

    def starting_equity_today(self, today: date) -> float:
        """Return the static starting equity (ignores ``today``)."""
        del today  # unused — provider is a fixed scalar
        return self._eq


class DailyLossBreaker:
    """Pre-trade circuit breaker on cumulative realized losses for the UTC day.

    The breaker computes ``realized_pnl_pct = realized_pnl_usd /
    starting_equity_usd`` and, if it is at or below ``floor_pct``, refuses new
    entries. Exits are always allowed.

    Example:
        >>> breaker = DailyLossBreaker(equity_provider, journal_reader, floor_pct=-0.03)
        >>> decision = breaker.check()
        >>> if not decision.can_open_new:
        ...     raise RuntimeError(decision.reason)

    Args:
        equity_provider: Source of the day's starting equity. Cached by date.
        journal_reader: Source of today's realized P&L total.
        floor_pct: Loss floor as a fraction of starting equity. Must be in
            ``[-0.20, 0.0]``.
        clock: Callable returning the current UTC :class:`datetime`. Override
            in tests.
        cache_seconds: Result of :meth:`check` is cached for this many seconds
            to avoid re-reading the journal on every signal. Cache is also
            invalidated on UTC day rollover.
    """

    def __init__(
        self,
        equity_provider: EquityProvider,
        journal_reader: JournalReader,
        floor_pct: float = -0.03,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cache_seconds: float = 30.0,
    ) -> None:
        if not (_FLOOR_MIN <= floor_pct <= _FLOOR_MAX):
            raise ValueError(
                f"floor_pct must be in [{_FLOOR_MIN}, {_FLOOR_MAX}], got {floor_pct}"
            )
        self._equity = equity_provider
        self._journal = journal_reader
        self._floor_pct = float(floor_pct)
        self._clock = clock
        self._cache_seconds = float(cache_seconds)

        # Cached state.
        self._cached_decision: DailyLossDecision | None = None
        self._cached_at: datetime | None = None
        # Starting equity is sticky-per-UTC-date: the first check on a new day
        # locks it in until midnight rolls over.
        self._equity_date: date | None = None
        self._equity_usd: float = 0.0

    def check(self) -> DailyLossDecision:
        """Return the current decision, using a short cache when valid."""
        now = self._clock()
        today = now.date()

        if self._cache_valid(now, today):
            assert self._cached_decision is not None  # for type checker
            return self._cached_decision

        # Day rollover or cold start: refresh the day's starting equity.
        if self._equity_date != today:
            self._equity_usd = float(self._equity.starting_equity_today(today))
            self._equity_date = today

        starting_equity = self._equity_usd
        realized_pnl = float(self._journal.realized_pnl_today(today))

        if starting_equity > 0:
            realized_pct = realized_pnl / starting_equity
        else:
            # Degenerate case: cannot compute a meaningful percentage. Allow
            # opens rather than block by accident.
            realized_pct = 0.0

        # Strict inequality: breaching the floor exactly BLOCKS new entries.
        can_open_new = realized_pct > self._floor_pct
        if can_open_new:
            reason = (
                f"Daily loss tracker OK: {realized_pct * 100:.1f}% realized vs "
                f"{self._floor_pct * 100:.1f}% floor"
            )
        else:
            reason = (
                f"Daily loss floor breached: {realized_pct * 100:.1f}% realized "
                f"exceeds {self._floor_pct * 100:.1f}% floor; "
                "new entries halted, exits still allowed"
            )

        decision = DailyLossDecision(
            can_open_new=can_open_new,
            can_exit=True,
            realized_pnl_usd=realized_pnl,
            realized_pnl_pct=realized_pct,
            floor_pct=self._floor_pct,
            starting_equity_usd=starting_equity,
            reason=reason,
            asof=now.isoformat(),
        )

        self._cached_decision = decision
        self._cached_at = now
        return decision

    def reset_cache(self) -> None:
        """Clear the cached decision so the next :meth:`check` recomputes."""
        self._cached_decision = None
        self._cached_at = None

    def _cache_valid(self, now: datetime, today: date) -> bool:
        """True iff the cached decision is still fresh and on the same UTC day."""
        if self._cached_decision is None or self._cached_at is None:
            return False
        if self._equity_date != today:
            # Day rolled over — invalidate.
            return False
        elapsed = (now - self._cached_at).total_seconds()
        return 0.0 <= elapsed < self._cache_seconds
