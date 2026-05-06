"""Read-only dashboard endpoints for refusal events.

A *refusal* is a first-class governance audit row recording every signal
the bot deliberately did NOT take, plus the reason code (reasoner halt,
risk cap, news blackout, etc.). See :mod:`src.journal.refusal_events` for
the producer side.

This router exposes:

  * ``GET /api/refusals/recent`` — paginated list of recent refusal events
    from the journal, optionally filtered by ``since`` (ISO8601) and
    ``reason``. Caps at 1000 results and is safe under partial/missing
    journal files (returns ``[]`` rather than 500).

The router is intentionally **read-only** and unauthenticated — same
threat model as the existing peer endpoints in :mod:`dashboard.api.main`
(``/api/portfolio``, ``/api/positions``, etc.). The FastAPI port should
not be reachable beyond localhost in v1; mutating endpoints require a
confirm-token, but pure GET routes do not.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from dashboard.api import journal_reader

log = logging.getLogger(__name__)

router = APIRouter()


# Tightly bound the result count. The dashboard paginates, and a single
# very large response can wedge the browser; cap at 1000 to keep the
# endpoint cheap even on a noisy journal day.
_MAX_LIMIT: int = 1000


class RefusalRecord(BaseModel):
    """One refusal event, normalized for the dashboard.

    Mirrors the journal envelope written by :func:`log_refusal`. Field
    types are permissive (``str | None``) because old/partial journal rows
    might be missing newer fields — we'd rather render incomplete history
    than 500 on a schema drift.
    """

    ts: str
    reason: str
    symbol: str | None = None
    side: str | None = None
    strategy: str | None = None
    agent: str | None = None
    signal_id: str | None = None
    detail: str = ""
    extra: dict[str, Any] | None = Field(default=None)


def _parse_since(raw: str | None) -> datetime | None:
    """Parse an ISO8601 ``since`` query param. Tolerates trailing ``Z``."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        # Bad input → treat as "no filter". The endpoint is best-effort;
        # we don't want a typo'd timestamp to 400 the dashboard.
        return None


def _normalize(event: dict[str, Any]) -> RefusalRecord | None:
    """Coerce a raw journal dict into a :class:`RefusalRecord`.

    Returns ``None`` if the row is malformed enough to be unusable (no
    ``reason`` or no ``ts``). Old/incomplete rows with missing optional
    fields are accepted.
    """
    if event.get("event") != "refusal":
        return None
    reason = event.get("reason")
    ts = event.get("ts")
    if not isinstance(reason, str) or not isinstance(ts, str):
        return None
    return RefusalRecord(
        ts=ts,
        reason=reason,
        symbol=event.get("symbol"),
        side=event.get("side"),
        strategy=event.get("strategy"),
        agent=event.get("agent"),
        signal_id=event.get("signal_id"),
        detail=event.get("detail") or "",
        extra=event.get("extra") if isinstance(event.get("extra"), dict) else None,
    )


@router.get("/api/refusals/recent", response_model=list[RefusalRecord])
async def refusals_recent(
    since: str | None = Query(None, description="ISO8601 lower-bound filter"),
    reason: str | None = Query(None, description="Filter by RefusalReason literal"),
    limit: int = Query(200, ge=1, le=_MAX_LIMIT),
) -> list[RefusalRecord]:
    """List recent refusal events from the journal.

    Args:
        since: Optional ISO8601 timestamp. Only refusals at or after this
            time are returned. Bad input is tolerated (treated as
            "no filter") — we never 400 the dashboard.
        reason: Optional :class:`~src.journal.refusal_events.RefusalReason`
            literal. Exact-match filter.
        limit: Hard cap on result count. Bounded by ``_MAX_LIMIT``.

    Returns:
        Newest-first list of :class:`RefusalRecord`. Empty list if the
        journal directory is missing or the day's file does not exist —
        never raises HTTP 500 on missing data.
    """
    try:
        since_dt = _parse_since(since)
        # If no since filter, default to today's journal. If since is
        # provided, walk from that date forward through today.
        today = datetime.now(UTC).date()
        if since_dt is not None:
            start = since_dt.date()
            # Guard against absurd lookbacks — cap at 365 days.
            earliest = today - timedelta(days=365)
            start = max(start, earliest)
        else:
            start = today
        events = journal_reader.read_events(start=start, end=today)
    except Exception as exc:
        log.debug("refusals: journal read failed, returning []: %s", exc)
        return []

    out: list[RefusalRecord] = []
    for raw in events:
        rec = _normalize(raw)
        if rec is None:
            continue
        if reason is not None and rec.reason != reason:
            continue
        if since_dt is not None:
            try:
                ev_ts = datetime.fromisoformat(rec.ts.replace("Z", "+00:00"))
                if ev_ts < since_dt:
                    continue
            except ValueError:
                # Malformed ts on the row — keep it; better to surface
                # than to silently drop.
                pass
        out.append(rec)

    # Newest first. String compare on ISO8601 UTC is order-correct.
    out.sort(key=lambda r: r.ts, reverse=True)
    return out[:limit]


__all__ = ["RefusalRecord", "router"]
