"""Read trades and other events from `journal/YYYY-MM-DD.jsonl` files."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src.config import PROJECT_ROOT

JOURNAL_DIR = PROJECT_ROOT / "journal"


def _iter_dates(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _read_journal_for(d: date) -> list[dict]:
    path = JOURNAL_DIR / f"{d.strftime('%Y-%m-%d')}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_events(
    *,
    start: date | None = None,
    end: date | None = None,
    event_filter: tuple[str, ...] = (),
) -> list[dict]:
    """Read all journal events between `start` and `end` (inclusive). UTC dates."""
    # JournalWriter strftime's filename in UTC; defaults must match.
    today_utc = datetime.now(UTC).date()
    start = start or today_utc - timedelta(days=30)
    end = end or today_utc
    out: list[dict] = []
    for d in _iter_dates(start, end):
        for event in _read_journal_for(d):
            if event_filter and event.get("event") not in event_filter:
                continue
            out.append(event)
    return out


def read_trades(*, start: date | None = None, end: date | None = None) -> list[dict]:
    """Trade-related events.

    Includes both legacy event names (``submit``, ``fill``, ``partial_fill``)
    used by the older execution path and the Round-8 pipeline names
    (``trade_submit``, ``trade_fill``, ``trade_partial_fill``) emitted by
    :class:`src.runtime.trade_pipeline.TradePipeline`. Without the new names
    here, real broker submissions journal correctly but never reach the UI.
    """
    return read_events(
        start=start,
        end=end,
        event_filter=(
            # Legacy names — kept for backward compat with older journals.
            "submit",
            "fill",
            "partial_fill",
            "submit_dry_run",
            # Pipeline names from src/runtime/trade_pipeline.py.
            "trade_submit",
            "trade_fill",
            "trade_partial_fill",
        ),
    )


def journal_today_path() -> Path:
    return JOURNAL_DIR / f"{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
