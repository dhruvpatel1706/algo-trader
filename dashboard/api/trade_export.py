"""CSV export for closed (and open) trades reconstructed from the JSONL journal.

The journal writes one event per state transition (``submit`` → ``fill`` /
``partial_fill`` → ``exit`` / ``trade_closed``), keyed by ``client_order_id``.
This router walks today's window of journal events, pairs each entry with its
closing event, and streams a flat CSV the user can download "just in case."

The endpoint is intentionally permissive — the goal is "give me all my
trades" — so:

* Defaults to the last 30 UTC days when ``from``/``to`` are unset.
* Open trades (no matching close event) are emitted with empty exit fields
  rather than dropped, so you can see what's still on the books.
* Malformed journal rows are *skipped and logged*, not raised — a single bad
  line should never poison the whole export.

Threat model is the same as the peer read-only routers in
:mod:`dashboard.api.main`: localhost-bound, unauthenticated, no mutation. The
returned blob is plain CSV with attachment headers so browsers will save it.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Response

from dashboard.api import journal_reader

log = logging.getLogger(__name__)

router = APIRouter()


# Pin column order — tests assert against this exact tuple. Reordering or
# adding columns is a breaking change for any downstream tooling that consumes
# the CSV (e.g. spreadsheet imports, accountant pivots), so think twice.
CSV_COLUMNS: tuple[str, ...] = (
    "ts",
    "symbol",
    "side",
    "qty",
    "entry_price",
    "exit_price",
    "pnl_usd",
    "pnl_pct",
    "pnl_r",
    "strategy",
    "agent",
    "broker_order_id",
    "client_order_id",
    "status",
    "cycle_id",
)

# Events to consider as "trade-shaped". ``submit_dry_run`` is included because
# the user explicitly wants every recorded trade; smoke/dry-run rows are still
# part of the audit trail.
_TRADE_EVENTS: tuple[str, ...] = (
    "submit",
    "submit_dry_run",
    "fill",
    "partial_fill",
    "exit",
    "trade_closed",
)

# Treat these as "closing" events when pairing entry → close by client_order_id.
_CLOSE_EVENTS: frozenset[str] = frozenset({"exit", "trade_closed"})

# Treat these as "opening / fill" events. ``fill`` and ``partial_fill`` carry
# the realized fill price for an entry; ``submit`` / ``submit_dry_run`` are the
# accepted intent without a fill price yet but are still useful as a stand-in
# entry timestamp when a fill event never made it to the journal.
_ENTRY_EVENTS: frozenset[str] = frozenset(
    {"submit", "submit_dry_run", "fill", "partial_fill"}
)


def _coerce_float(value: Any) -> float | None:
    """Parse a numeric-ish value to float, or None on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: Any) -> str:
    """Stringify a value, returning ``""`` for None."""
    if value is None:
        return ""
    return str(value)


def _entry_priority(event_name: str) -> int:
    """Lower number wins when picking the entry event for a client_order_id.

    A real ``fill`` always beats a ``submit`` — fills carry the realized price.
    Inside fills, the first ``fill`` (vs. follow-on ``partial_fill``) is what we
    record as the entry, so plain ``fill`` ranks above ``partial_fill``.
    """
    return {"fill": 0, "partial_fill": 1, "submit": 2, "submit_dry_run": 3}.get(
        event_name, 9
    )


def _build_rows(events: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Pair entry events with their close by ``client_order_id``.

    Returns one row per unique ``client_order_id``. Each row is fully
    string-typed and ready for ``csv.DictWriter``.
    """
    entries: dict[str, dict[str, Any]] = {}
    closes: dict[str, dict[str, Any]] = {}
    # Preserve the order that client_order_ids first appear so the CSV reflects
    # journal-time ordering (oldest first), which matches user expectations and
    # gives stable output for round-trip tests.
    order: list[str] = []

    for raw in events:
        if not isinstance(raw, dict):
            continue
        event_name = raw.get("event")
        if event_name not in _TRADE_EVENTS:
            continue
        coid = raw.get("client_order_id")
        if not isinstance(coid, str) or not coid:
            # No idempotency key → cannot pair; skip rather than emit a
            # malformed row.
            log.debug("trade_export: skipping event with no client_order_id: %r", raw)
            continue

        if coid not in entries and coid not in closes:
            order.append(coid)

        if event_name in _CLOSE_EVENTS:
            # Last close wins (later events overwrite earlier) — matches how
            # journal replay would read it.
            closes[coid] = raw
        elif event_name in _ENTRY_EVENTS:
            existing = entries.get(coid)
            if existing is None or _entry_priority(event_name) < _entry_priority(
                existing.get("event", "")
            ):
                entries[coid] = raw

    rows: list[dict[str, str]] = []
    for coid in order:
        entry = entries.get(coid)
        close = closes.get(coid)
        try:
            row = _row_from_pair(coid, entry, close)
        except Exception as exc:
            # Defensive: a single bad event should never crash the export.
            log.warning(
                "trade_export: skipping malformed trade for %s: %s", coid, exc
            )
            continue
        if row is None:
            continue
        rows.append(row)
    return rows


def _row_from_pair(
    client_order_id: str,
    entry: dict[str, Any] | None,
    close: dict[str, Any] | None,
) -> dict[str, str] | None:
    """Compose a single CSV row dict from a paired (entry, close).

    Returns ``None`` if neither side carries enough data to be useful (i.e.
    no symbol).
    """
    primary = entry or close
    if primary is None:
        return None

    # Prefer the close timestamp's primary as ts? No — ``ts`` is the *entry*
    # time per the spec, so we prefer the entry event's ts if available, then
    # fall back to the close's.
    ts = (entry or {}).get("ts") or (close or {}).get("ts") or ""

    symbol = primary.get("symbol") or (close or {}).get("symbol")
    if not isinstance(symbol, str) or not symbol:
        # No symbol = not actually a trade row.
        return None

    side = primary.get("side") or (close or {}).get("side") or ""
    qty = primary.get("qty")
    if qty is None and close is not None:
        qty = close.get("qty")

    # Entry price — `fill_price` (broker normalized) is preferred, then the
    # historic `price` / `limit_price` fields. Empty if we only have a submit
    # without any fill price yet.
    entry_price = (
        (entry or {}).get("fill_price")
        or (entry or {}).get("price")
        or (entry or {}).get("avg_fill_price")
    )

    if close is not None:
        exit_price = (
            close.get("exit_price")
            or close.get("fill_price")
            or close.get("price")
        )
        pnl_usd = close.get("pnl_usd") if "pnl_usd" in close else close.get("pnl")
        pnl_pct = close.get("pnl_pct")
        pnl_r = close.get("pnl_r") if "pnl_r" in close else close.get("r_multiple")
        status = close.get("status") or "filled"
    else:
        exit_price = None
        pnl_usd = None
        pnl_pct = None
        pnl_r = None
        status = (entry or {}).get("status") or "open"

    strategy = (
        primary.get("strategy")
        or primary.get("strategy_tag")
        or (close or {}).get("strategy")
        or ""
    )
    agent = primary.get("agent") or (close or {}).get("agent") or ""
    broker_order_id = (
        primary.get("broker_order_id") or (close or {}).get("broker_order_id") or ""
    )
    cycle_id = primary.get("cycle_id") or (close or {}).get("cycle_id") or ""

    def _fmt_num(v: Any) -> str:
        f = _coerce_float(v)
        return "" if f is None else format(f, "g")

    return {
        "ts": _coerce_str(ts),
        "symbol": _coerce_str(symbol),
        "side": _coerce_str(side),
        "qty": _fmt_num(qty),
        "entry_price": _fmt_num(entry_price),
        "exit_price": _fmt_num(exit_price),
        "pnl_usd": _fmt_num(pnl_usd),
        "pnl_pct": _fmt_num(pnl_pct),
        "pnl_r": _fmt_num(pnl_r),
        "strategy": _coerce_str(strategy),
        "agent": _coerce_str(agent),
        "broker_order_id": _coerce_str(broker_order_id),
        "client_order_id": _coerce_str(client_order_id),
        "status": _coerce_str(status),
        "cycle_id": _coerce_str(cycle_id),
    }


def _filename(start: date, end: date) -> str:
    return f'trades_{start.isoformat()}_{end.isoformat()}.csv'


@router.get("/api/trades/export.csv")
async def export_trades_csv(
    # FastAPI uses Query() / Depends() in argument defaults idiomatically; the
    # B008 warning here is a false positive in our codebase. main.py has a
    # blanket B008 ignore; we silence per-line here to keep the lint surface
    # minimal for this small additive router.
    from_: date | None = Query(None, alias="from", description="UTC date lower bound"),  # noqa: B008
    to: date | None = Query(None, description="UTC date upper bound"),  # noqa: B008
    strategy: str | None = Query(None, description="Exact strategy filter"),
) -> Response:
    """Stream a CSV file of closed (and open) trades from the journal.

    Args:
        from_: Inclusive lower-bound UTC date. Defaults to 30 days back.
        to: Inclusive upper-bound UTC date. Defaults to today.
        strategy: Exact-match filter on the journal's ``strategy`` /
            ``strategy_tag`` field. Empty filter (``None``) returns all.

    Returns:
        FastAPI :class:`Response` with ``text/csv`` body. On any unexpected
        read error we still return a 200 with just the header row — callers
        rely on this endpoint to *always* download something rather than 500.
    """
    today = datetime.now(UTC).date()
    end = to or today
    start = from_ or (end - timedelta(days=30))
    if start > end:
        # Tolerate inverted ranges by swapping; user intent is clearly "give me
        # the window between these two dates."
        start, end = end, start

    try:
        events = journal_reader.read_events(
            start=start, end=end, event_filter=_TRADE_EVENTS
        )
    except Exception as exc:
        log.warning("trade_export: journal read failed, returning header-only: %s", exc)
        events = []

    rows = _build_rows(events)
    if strategy is not None:
        rows = [r for r in rows if r["strategy"] == strategy]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        try:
            writer.writerow(row)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("trade_export: failed to write row %s: %s", row.get("client_order_id"), exc)
            continue

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(start, end)}"',
            "Cache-Control": "no-cache",
        },
    )


__all__ = ["CSV_COLUMNS", "router"]
