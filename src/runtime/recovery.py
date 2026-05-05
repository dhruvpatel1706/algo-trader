"""Boot-time reconciliation between the journal and broker state.

On bot startup, replays today's JSONL journal to reconstruct the open
positions implied by APPROVE/FILL/EXIT events, then compares that against
what the broker actually reports. Any divergence escalates to a ``halt``
severity so the operator (or a watchdog) can intervene before the bot
trades on a stale view of the world.

The journal format follows :mod:`src.journal.writer`: one JSON record per
line, written via ``JournalWriter.write({"event": ..., ...})``. Recovery
inspects only events relevant to position state. Unknown events are
ignored.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

Severity = Literal["ok", "warn", "halt"]


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Result of comparing journal-implied positions against broker state.

    Attributes
    ----------
    expected:
        Symbol -> qty implied by today's journal (signed; negative = short).
    actual:
        Symbol -> qty reported by the broker.
    divergence_count:
        Number of symbols where ``expected[s] != actual.get(s, 0)``.
    severity:
        ``"ok"`` if no divergence, ``"halt"`` otherwise. We don't currently
        emit ``"warn"`` from boot reconcile - any divergence on boot is
        treated as serious because it implies an undetected fill or a
        broker-side liquidation we didn't journal.
    notes:
        Human-readable summary; safe to surface in the dashboard or logs.
    """

    expected: dict[str, int] = field(default_factory=dict)
    actual: dict[str, int] = field(default_factory=dict)
    divergence_count: int = 0
    severity: Severity = "ok"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Journal -> expected positions
# ---------------------------------------------------------------------------

# Events that move open-position state. We accept several names so the
# reconcile is forgiving as the journal schema evolves.
_FILL_EVENTS: frozenset[str] = frozenset(
    {"fill", "FILL", "broker_fill", "approve_fill", "APPROVE_FILL"}
)
_EXIT_EVENTS: frozenset[str] = frozenset(
    {"exit", "EXIT", "close", "CLOSE", "flatten", "FLATTEN"}
)


def _replay(events: list[dict[str, Any]]) -> dict[str, int]:
    """Compute symbol -> signed qty implied by an event sequence."""
    qty: dict[str, int] = {}

    for ev in events:
        kind = str(ev.get("event") or ev.get("type") or "")
        symbol = ev.get("symbol")
        if not symbol:
            continue

        if kind in _FILL_EVENTS:
            side = str(ev.get("side", "buy")).lower()
            try:
                n = int(float(ev.get("qty", 0)))
            except (TypeError, ValueError):
                continue
            if n <= 0:
                continue
            delta = n if side == "buy" else -n
            qty[symbol] = qty.get(symbol, 0) + delta

        elif kind in _EXIT_EVENTS:
            # An EXIT event closes whatever position we have for the symbol.
            # If the journal carries an explicit qty we honor it; otherwise
            # we zero the symbol.
            try:
                n = int(float(ev.get("qty", 0)))
            except (TypeError, ValueError):
                n = 0
            if n > 0:
                # EXIT side defaults to opposite of current sign.
                side = str(ev.get("side", "")).lower()
                if side == "sell" or (side == "" and qty.get(symbol, 0) > 0):
                    qty[symbol] = qty.get(symbol, 0) - n
                else:
                    qty[symbol] = qty.get(symbol, 0) + n
            else:
                qty[symbol] = 0

    # Drop zero entries; "no position" should not show up as a key.
    return {s: q for s, q in qty.items() if q != 0}


def _read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("skipping malformed journal line in %s", path)
    except OSError as e:
        log.warning("could not read journal %s: %s", path, e)
    return out


# ---------------------------------------------------------------------------
# Broker -> actual positions
# ---------------------------------------------------------------------------


def _fetch_raw_positions(broker: Any) -> Any:
    """Call the first available position-fetching method, swallowing errors."""
    for attr in ("get_all_positions", "get_positions"):
        fn = getattr(broker, attr, None)
        if not callable(fn):
            continue
        try:
            return fn()
        except Exception as e:
            log.warning("broker.%s() failed: %s", attr, e)
            return None
    log.warning("broker has no get_positions / get_all_positions; treating as empty")
    return None


def _normalize_positions(raw: Any) -> dict[str, int]:
    """Convert whatever the broker returned into ``symbol -> signed qty``."""
    if raw is None:
        return {}

    out: dict[str, int] = {}
    if isinstance(raw, dict):
        for symbol, p in raw.items():
            qty = _coerce_qty(p)
            if qty is not None:
                out[str(symbol)] = qty
        return out

    try:
        iterable = list(raw)
    except TypeError:
        return {}

    for p in iterable:
        symbol = p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None)
        qty = _coerce_qty(p)
        if not symbol or qty is None or qty == 0:
            continue
        out[str(symbol)] = qty
    return out


def _broker_positions(broker: Any) -> dict[str, int]:
    """Best-effort extraction of ``symbol -> qty`` from a broker.

    Supports several shapes so we work with both :class:`PaperBroker`-style
    objects and the dashboard's :class:`BrokerProxy`:

    - ``broker.get_all_positions()`` (alpaca-py raw)
    - ``broker.get_positions()`` returning ``list[dict]`` or ``dict[str, X]``
    """
    return _normalize_positions(_fetch_raw_positions(broker))


def _coerce_qty(p: Any) -> int | None:
    """Pull a signed integer qty out of a dict / object position record."""
    if isinstance(p, dict):
        if "qty" in p:
            try:
                base = int(float(p["qty"]))
            except (TypeError, ValueError):
                return None
        elif "size" in p:
            try:
                base = int(float(p["size"]))
            except (TypeError, ValueError):
                return None
        else:
            return None
        side = str(p.get("side", "long")).lower()
        return -abs(base) if side == "short" else abs(base)

    qty_attr = getattr(p, "qty", None)
    if qty_attr is None:
        return None
    try:
        base = int(float(qty_attr))
    except (TypeError, ValueError):
        return None
    side = str(getattr(p, "side", "long")).lower()
    return -abs(base) if side == "short" else abs(base)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def reconcile_on_boot(
    journal_dir: Path,
    broker: Any,  # PaperBroker / LiveBroker / BrokerProxy
    today: date | None = None,
) -> ReconcileReport:
    """Compare today's journal-implied positions against the broker.

    Parameters
    ----------
    journal_dir:
        Directory containing the daily JSONL files.
    broker:
        Anything with ``get_all_positions()`` or ``get_positions()``.
    today:
        Date to reconcile. Defaults to today's date in UTC, matching what
        :class:`src.journal.writer.JournalWriter.path_for` writes.
    """
    d = today or datetime.now(UTC).date()
    journal_path = Path(journal_dir) / f"{d.isoformat()}.jsonl"

    events = _read_journal(journal_path)
    expected = _replay(events)
    actual = _broker_positions(broker)

    symbols = set(expected) | set(actual)
    divergent: list[str] = []
    for s in symbols:
        if expected.get(s, 0) != actual.get(s, 0):
            divergent.append(s)

    if not divergent:
        notes = (
            f"reconcile ok: {len(expected)} expected position(s), {len(actual)} broker position(s)"
        )
        return ReconcileReport(
            expected=expected,
            actual=actual,
            divergence_count=0,
            severity="ok",
            notes=notes,
        )

    diffs = ", ".join(
        f"{s}: journal={expected.get(s, 0)} broker={actual.get(s, 0)}" for s in sorted(divergent)
    )
    notes = f"divergence on boot ({len(divergent)} symbol(s)): {diffs}"
    return ReconcileReport(
        expected=expected,
        actual=actual,
        divergence_count=len(divergent),
        severity="halt",
        notes=notes,
    )
