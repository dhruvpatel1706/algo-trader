"""Persistent SQLite-backed memory for the governance agent.

The governance agent runs as a meta-supervisor: it reads agent statuses and
emits recommendations. Without persistence, every run starts fresh — the
agent has no way to notice "this strategy has drifted three days in a row"
or "we already halted this once last week". This module provides a tiny,
append-only memory that survives restarts and can be folded into prompt
context for longitudinal reasoning.

Schema is intentionally minimal:
  id          autoincrement primary key
  ts          UTC ISO-8601 string (sortable, easy to inspect by hand)
  kind        observation | decision | drift | halt | promote
  target      strategy/agent name, or NULL for portfolio-wide notes
  content     human-readable summary, capped at 4000 chars
  metadata    JSON blob (free-form; e.g. {"max_dd": 0.08, "win_rate": 0.41})

Design choices:
  - sqlite3 stdlib only; storage at live/governance_memory.db
  - WAL journaling so concurrent readers (dashboard, replay) don't block
    the writer and vice versa
  - autocommit (isolation_level=None) — every add() is durable immediately;
    the bot is a slow-tick agent, no batch-insert hot path to optimise for
  - content truncated to 4000 chars with "..." suffix to keep prompt budget
    predictable and stop someone dumping a stack trace into memory
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from src.config import PROJECT_ROOT

MemoryKind = Literal["observation", "decision", "drift", "halt", "promote"]
_VALID_KINDS: frozenset[str] = frozenset(
    ("observation", "decision", "drift", "halt", "promote")
)
_CONTENT_MAX = 4000
_TRUNCATE_SUFFIX = "..."


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """One persisted memory row, decoded into Python types."""

    id: int
    ts: datetime
    kind: MemoryKind
    target_strategy: str | None
    content: str
    metadata: dict[str, Any]


def _truncate(content: str) -> str:
    """Cap content at _CONTENT_MAX chars, appending an ellipsis when cut."""
    if len(content) <= _CONTENT_MAX:
        return content
    keep = _CONTENT_MAX - len(_TRUNCATE_SUFFIX)
    return content[:keep] + _TRUNCATE_SUFFIX


class GovernanceMemory:
    """SQLite-backed long-term memory for governance_agent.

    Stores OBSERVATIONS (what was seen — drift, drawdown, coherence drop)
    and DECISIONS (what was decided — halt, kill, promote). The recent N
    entries can be rendered as a compact text block via summary_for_prompt
    so the LLM sees prior state on every governance run.

    Concurrency: WAL journal mode lets multiple readers co-exist with one
    writer without blocking. Two GovernanceMemory instances pointed at the
    same db path will both see each other's writes after the writer's
    statement returns (autocommit is on).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = Path(db_path) if db_path is not None else (
            PROJECT_ROOT / "live" / "governance_memory.db"
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None -> autocommit; every add() is immediately durable
        self._conn = sqlite3.connect(str(self._path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_entries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL,
                kind            TEXT NOT NULL,
                target_strategy TEXT,
                content         TEXT NOT NULL,
                metadata        TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_ts ON memory_entries(ts DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory_entries(kind)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_target "
            "ON memory_entries(target_strategy)"
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add(
        self,
        kind: MemoryKind,
        content: str,
        target_strategy: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Insert a memory entry. Returns the new row id.

        Content over 4000 chars is truncated with a "..." suffix so the
        on-disk size and prompt budget remain predictable.
        """
        if kind not in _VALID_KINDS:
            raise ValueError(
                f"invalid kind {kind!r}; must be one of {sorted(_VALID_KINDS)}"
            )
        ts = datetime.now(UTC).isoformat()
        body = _truncate(content)
        meta_json = json.dumps(metadata or {}, separators=(",", ":"), default=str)
        cur = self._conn.execute(
            "INSERT INTO memory_entries (ts, kind, target_strategy, content, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, kind, target_strategy, body, meta_json),
        )
        rowid = cur.lastrowid
        if rowid is None:  # pragma: no cover - sqlite always returns one
            raise RuntimeError("sqlite did not return a rowid for insert")
        return int(rowid)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def recent(
        self,
        limit: int = 50,
        kinds: Iterable[str] | None = None,
        target_strategy: str | None = None,
    ) -> list[MemoryEntry]:
        """Return up to `limit` entries, newest first.

        `kinds` filters by entry kind; `target_strategy` filters by the
        strategy the entry is about. Both filters are optional.
        """
        sql = (
            "SELECT id, ts, kind, target_strategy, content, metadata "
            "FROM memory_entries"
        )
        clauses: list[str] = []
        params: list[Any] = []
        if kinds is not None:
            kinds_list = list(kinds)
            if not kinds_list:
                return []
            placeholders = ",".join("?" * len(kinds_list))
            clauses.append(f"kind IN ({placeholders})")
            params.extend(kinds_list)
        if target_strategy is not None:
            clauses.append("target_strategy = ?")
            params.append(target_strategy)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def summary_for_prompt(
        self,
        max_chars: int = 8000,
        lookback_days: int = 30,
    ) -> str:
        """Render memory as a compact text block for LLM injection.

        Entries are grouped by kind (decisions first — they're the most
        useful prior context), newest-first within each group. Output is
        hard-capped at `max_chars`; the cap counts the rendered text only,
        not the headings.
        """
        cutoff = datetime.now(UTC).timestamp() - lookback_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
        rows = self._conn.execute(
            "SELECT id, ts, kind, target_strategy, content, metadata "
            "FROM memory_entries WHERE ts >= ? ORDER BY id DESC",
            (cutoff_iso,),
        ).fetchall()
        if not rows:
            return ""

        # Group by kind preserving newest-first order within each bucket.
        order = ("decision", "halt", "promote", "drift", "observation")
        buckets: dict[str, list[tuple[Any, ...]]] = {k: [] for k in order}
        for r in rows:
            kind = r[2]
            if kind in buckets:
                buckets[kind].append(r)

        lines: list[str] = []
        used = 0
        budget_exhausted = False
        for kind in order:
            kind_rows = buckets[kind]
            if not kind_rows:
                continue
            header = f"# {kind.upper()}"
            if used + len(header) + 1 > max_chars:
                budget_exhausted = True
                break
            lines.append(header)
            used += len(header) + 1
            for row in kind_rows:
                line = self._format_row(row)
                if used + len(line) + 1 > max_chars:
                    budget_exhausted = True
                    break
                lines.append(line)
                used += len(line) + 1
            if budget_exhausted:
                break
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def compact(self, keep_days: int = 365) -> int:
        """Drop entries older than `keep_days`. Returns rows removed.

        keep_days=0 wipes everything (useful for tests).
        """
        cutoff = datetime.now(UTC).timestamp() - keep_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
        cur = self._conn.execute(
            "DELETE FROM memory_entries WHERE ts < ?", (cutoff_iso,)
        )
        return int(cur.rowcount or 0)

    def close(self) -> None:
        """Close the underlying connection. Idempotent."""
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            # Already closed.
            pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: tuple[Any, ...]) -> MemoryEntry:
        rid, ts_str, kind, target, content, meta_json = row
        try:
            metadata = json.loads(meta_json) if meta_json else {}
        except json.JSONDecodeError:
            metadata = {}
        return MemoryEntry(
            id=int(rid),
            ts=datetime.fromisoformat(ts_str),
            kind=kind,
            target_strategy=target,
            content=content,
            metadata=metadata,
        )

    @staticmethod
    def _format_row(row: tuple[Any, ...]) -> str:
        _, ts_str, _, target, content, _ = row
        target_part = f"[{target}] " if target else ""
        # ts is ISO-8601 with seconds precision; trim microseconds for prompt brevity
        ts_short = ts_str.split(".")[0] if "." in ts_str else ts_str
        return f"- {ts_short} {target_part}{content}"
