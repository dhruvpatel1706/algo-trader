"""Market microstructure phase classifier.

Classifies a timezone-aware datetime into a market microstructure phase
(``pre_market``, ``open``, ``midday``, ``close``, ``after_hours``,
``closed``) so that downstream reasoning (e.g. autonomous reasoner,
strategy gating) can make phase-aware decisions.

Phases are anchored to America/New_York time and the NYSE trading
calendar for equity-class assets. Crypto is treated as 24/7. FX is
24/5 with weekend window.

Pure functions, stdlib only — safe to call from anywhere.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

__all__ = [
    "AssetClass",
    "MarketPhase",
    "current_phase",
    "is_market_open",
    "phase_posture",
]

MarketPhase = Literal[
    "pre_market", "open", "midday", "close", "after_hours", "closed"
]
AssetClass = Literal[
    "equity", "crypto", "gold", "silver", "bonds", "fx", "options"
]

_NY = ZoneInfo("America/New_York")

# NYSE-aligned full-day holidays for 2025 and 2026 (observed dates).
# 2026 note: Jul 4 is a Saturday so the observed holiday is Friday 2026-07-03.
# TODO: handle early-close days (1pm ET on day-before-Thanksgiving, day-after-
# Thanksgiving, Christmas Eve, Independence Day eve when applicable). v1
# treats those as regular full sessions.
_US_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2025
        date(2025, 1, 1),  # New Year's Day
        date(2025, 1, 20),  # MLK Day
        date(2025, 2, 17),  # Presidents Day
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 26),  # Memorial Day
        date(2025, 6, 19),  # Juneteenth
        date(2025, 7, 4),  # Independence Day
        date(2025, 9, 1),  # Labor Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas
        # 2026
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # MLK Day
        date(2026, 2, 16),  # Presidents Day
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (observed: Jul 4 is Saturday)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    }
)

# Equity / NYSE-aligned phase boundaries in America/New_York wall time.
_PRE_MARKET_START = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_FIRST_30_MIN_END = time(10, 0)
_MIDDAY_END = time(15, 0)
_REGULAR_CLOSE = time(16, 0)
_AFTER_HOURS_END = time(20, 0)

# Asset classes that follow the NYSE/equity schedule for v1.
_NYSE_ALIGNED: frozenset[str] = frozenset(
    {"equity", "gold", "silver", "bonds", "options"}
)

_PHASE_POSTURES: dict[MarketPhase, str] = {
    "pre_market": (
        "Pre-market session — thin liquidity, news-driven gaps. "
        "Lean defensive on new entries."
    ),
    "open": (
        "First 30min after cash open — heightened volatility, "
        "gap fills and rejections in play. Be selective."
    ),
    "midday": (
        "Midday liquidity window — quieter tape, "
        "mean-reversion edge often strongest here."
    ),
    "close": (
        "Power hour into the cash close — MOC imbalances develop, "
        "late breakouts can fail abruptly."
    ),
    "after_hours": (
        "After-hours session — earnings reactions and headline risk. "
        "Lean defensive."
    ),
    "closed": (
        "Market is closed. No new positions; only manage existing ones "
        "via overnight broker rules."
    ),
}


def _require_aware(ts: datetime) -> None:
    """Raise ``ValueError`` if ``ts`` is naive."""
    if ts.tzinfo is None:
        raise ValueError("ts must be timezone-aware")


def _is_us_trading_day(d: date) -> bool:
    """Return ``True`` if ``d`` is a US equity trading day.

    A trading day is a weekday (Mon-Fri) that is not a hardcoded
    full-day holiday.
    """
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return d not in _US_HOLIDAYS


def _equity_phase(ts_ny: datetime) -> MarketPhase:
    """Classify an equity microstructure phase from NY-localized ``ts_ny``.

    Boundaries are right-open: ``[start, end)``.
    """
    if not _is_us_trading_day(ts_ny.date()):
        return "closed"

    t = ts_ny.time()
    if t < _PRE_MARKET_START:
        return "closed"
    if t < _REGULAR_OPEN:
        return "pre_market"
    if t < _FIRST_30_MIN_END:
        return "open"
    if t < _MIDDAY_END:
        return "midday"
    if t < _REGULAR_CLOSE:
        return "close"
    if t < _AFTER_HOURS_END:
        return "after_hours"
    return "closed"


def _fx_phase(ts_ny: datetime) -> MarketPhase:
    """Classify an FX phase from NY-localized ``ts_ny``.

    FX trades 24/5 with a weekend window. v1 simplification: treat the
    full Saturday (NY local) as ``closed`` and keep Sunday (and the
    rest of the week) as ``open``. This is a coarse approximation of
    the standard Sat 17:00 ET → Sun 17:00 ET halt.
    """
    if ts_ny.weekday() == 5:  # Saturday in NY local
        return "closed"
    return "open"


def current_phase(ts: datetime, asset_class: AssetClass = "equity") -> MarketPhase:
    """Return the microstructure phase for ``ts`` and ``asset_class``.

    Args:
        ts: Timezone-aware datetime. Naive inputs raise ``ValueError``.
            The instant is converted to America/New_York internally.
        asset_class: Asset class to classify against. Crypto is 24/7.
            FX is 24/5 (with weekend window). All other classes use the
            NYSE-aligned schedule for v1.

    Returns:
        One of ``pre_market``, ``open``, ``midday``, ``close``,
        ``after_hours``, ``closed``.

    Raises:
        ValueError: If ``ts`` has no ``tzinfo``.
    """
    _require_aware(ts)
    ts_ny = ts.astimezone(_NY)

    if asset_class == "crypto":
        return "open"
    if asset_class == "fx":
        return _fx_phase(ts_ny)
    if asset_class in _NYSE_ALIGNED:
        return _equity_phase(ts_ny)
    # Defensive fallback — should be unreachable given the Literal type.
    return _equity_phase(ts_ny)


def phase_posture(phase: MarketPhase) -> str:
    """Return a short, human-readable trading posture for ``phase``.

    The returned strings are stable and intended to be embedded verbatim
    into LLM prompts so the reasoner can adapt its judgment to the
    current microstructure context.

    Args:
        phase: Market microstructure phase.

    Returns:
        Non-empty posture description.
    """
    return _PHASE_POSTURES[phase]


def is_market_open(ts: datetime, asset_class: AssetClass = "equity") -> bool:
    """Return ``True`` if a regular trading session is active.

    "Open" here means the regular session (open / midday / close) — it
    does NOT include pre-market or after-hours, which are returned as
    ``False`` so callers gating order entry get the conservative answer
    by default.

    Args:
        ts: Timezone-aware datetime. Naive inputs raise ``ValueError``.
        asset_class: Asset class to classify against.

    Returns:
        ``True`` if the regular session is in progress, else ``False``.

    Raises:
        ValueError: If ``ts`` has no ``tzinfo``.
    """
    return current_phase(ts, asset_class) in {"open", "midday", "close"}
