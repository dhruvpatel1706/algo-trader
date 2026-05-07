"""SQLite-backed persistence for :class:`TradeMemory` records.

Vectors are stored as raw float32 BLOBs via ``array.array('f')``, which
keeps each row compact (1536 floats x 4 bytes ~= 6 kB) and avoids the
need for any extra dependency. Linear cosine scan in Python is fine up
to a few thousand records; we'll have far fewer in v1.

Schema is created lazily on first connection (``CREATE ... IF NOT
EXISTS``) so deployment is a no-op — the DB file is written into
``live/`` (gitignored) and is owned by this process.
"""

from __future__ import annotations

import array
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from src.memory.models import TradeMemory

DEFAULT_DB_PATH: Path = Path("live/memory.db")


class MemoryStoreError(RuntimeError):
    """Raised when the store fails an integrity check or a write."""


class MemoryStore:
    """Persistent SQLite store for :class:`TradeMemory` rows.

    The connection is opened in autocommit mode (``isolation_level=None``)
    with WAL journaling, mirroring the pattern used by
    :mod:`src.agents.governance_memory`. WAL allows the dashboard or a
    replay tool to read concurrently with the writer.

    Use :meth:`add` for single inserts, :meth:`add_batch` for many at
    once (atomic — either all rows are written or none are), and
    :meth:`update_outcome` to fill in P&L / R / label after a trade
    closes. The schema indexes ``ts``, ``strategy``, and ``symbol`` so
    the recall hot path can pre-filter cheaply.
    """

    _SCHEMA_SQL: str = """
        CREATE TABLE IF NOT EXISTS trade_memory (
            trade_id        TEXT PRIMARY KEY,
            ts              TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            side            TEXT NOT NULL,
            strategy        TEXT NOT NULL,
            narrative       TEXT NOT NULL,
            outcome_pnl_usd REAL,
            outcome_r       REAL,
            outcome_label   TEXT,
            embedding       BLOB NOT NULL,
            embedding_dim   INTEGER NOT NULL
        )
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._path: Path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` is required because APScheduler runs
        # each scheduled job on a worker thread out of its default pool —
        # the MemoryStore is constructed on the main thread (in run_bot.py)
        # but recall/insert is called from the reasoner running on a job
        # thread. Without this flag every reasoner call logs
        # ``SQLite objects created in a thread can only be used in that
        # same thread`` and the memory layer goes blind. WAL + NORMAL plus
        # the implicit row-level locking in sqlite3 make this safe for our
        # write rate (a handful per minute).
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self._path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._conn.execute(self._SCHEMA_SQL)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_memory_ts "
            "ON trade_memory(ts)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_memory_strategy "
            "ON trade_memory(strategy)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_memory_symbol "
            "ON trade_memory(symbol)"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _embedding_to_blob(vec: list[float]) -> bytes:
        return array.array("f", vec).tobytes()

    @staticmethod
    def _blob_to_embedding(blob: bytes) -> list[float]:
        arr = array.array("f")
        arr.frombytes(blob)
        return list(arr)

    @staticmethod
    def _row_to_memory(row: sqlite3.Row | tuple) -> TradeMemory:
        (
            trade_id,
            ts_iso,
            symbol,
            side,
            strategy,
            narrative,
            outcome_pnl_usd,
            outcome_r,
            outcome_label,
            blob,
            _embedding_dim,
        ) = row
        ts = datetime.fromisoformat(ts_iso)
        return TradeMemory(
            trade_id=trade_id,
            ts=ts,
            symbol=symbol,
            side=side,
            strategy=strategy,
            narrative=narrative,
            outcome_pnl_usd=outcome_pnl_usd,
            outcome_r=outcome_r,
            outcome_label=outcome_label,
            embedding=MemoryStore._blob_to_embedding(blob),
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block in a manual transaction.

        Autocommit means ``BEGIN`` / ``COMMIT`` are explicit; we wrap so
        :meth:`add_batch` is all-or-nothing.
        """
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add(self, mem: TradeMemory) -> None:
        """Insert one memory row.

        Raises :class:`MemoryStoreError` on duplicate ``trade_id`` or
        any other SQLite integrity error.
        """
        try:
            self._conn.execute(
                """
                INSERT INTO trade_memory (
                    trade_id, ts, symbol, side, strategy, narrative,
                    outcome_pnl_usd, outcome_r, outcome_label,
                    embedding, embedding_dim
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mem.trade_id,
                    mem.ts.isoformat(),
                    mem.symbol,
                    mem.side,
                    mem.strategy,
                    mem.narrative,
                    mem.outcome_pnl_usd,
                    mem.outcome_r,
                    mem.outcome_label,
                    self._embedding_to_blob(mem.embedding),
                    len(mem.embedding),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise MemoryStoreError(
                f"failed to insert trade_memory row trade_id={mem.trade_id!r}: {exc}"
            ) from exc

    def add_batch(self, mems: list[TradeMemory]) -> None:
        """Insert many memory rows atomically.

        If any row fails, the entire batch is rolled back. Useful for
        backfilling historical trades from the journal in one shot.
        """
        if not mems:
            return
        rows = [
            (
                m.trade_id,
                m.ts.isoformat(),
                m.symbol,
                m.side,
                m.strategy,
                m.narrative,
                m.outcome_pnl_usd,
                m.outcome_r,
                m.outcome_label,
                self._embedding_to_blob(m.embedding),
                len(m.embedding),
            )
            for m in mems
        ]
        try:
            with self._transaction() as conn:
                conn.executemany(
                    """
                    INSERT INTO trade_memory (
                        trade_id, ts, symbol, side, strategy, narrative,
                        outcome_pnl_usd, outcome_r, outcome_label,
                        embedding, embedding_dim
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        except sqlite3.IntegrityError as exc:
            raise MemoryStoreError(
                f"add_batch failed (rolled back): {exc}"
            ) from exc

    def update_outcome(
        self,
        trade_id: str,
        pnl_usd: float,
        r: float,
        label: str,
    ) -> None:
        """Mutate the outcome fields for ``trade_id``.

        Leaves all other columns (including the embedding) untouched.
        Silently no-ops if ``trade_id`` is unknown — callers that need
        to detect a missing row should call :meth:`get` first.
        """
        self._conn.execute(
            """
            UPDATE trade_memory
            SET outcome_pnl_usd = ?, outcome_r = ?, outcome_label = ?
            WHERE trade_id = ?
            """,
            (pnl_usd, r, label, trade_id),
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    _SELECT_ALL_BASE: str = (
        "SELECT trade_id, ts, symbol, side, strategy, narrative, "
        "outcome_pnl_usd, outcome_r, outcome_label, "
        "embedding, embedding_dim FROM trade_memory"
    )
    _SELECT_BY_ID: str = (
        "SELECT trade_id, ts, symbol, side, strategy, narrative, "
        "outcome_pnl_usd, outcome_r, outcome_label, "
        "embedding, embedding_dim FROM trade_memory WHERE trade_id = ?"
    )

    def all(
        self,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[TradeMemory]:
        """Return rows ordered by entry time descending (newest first).

        Args:
            since: If set, exclude rows with ``ts < since``. Comparison
                is lexicographic on the ISO-8601 string, which is
                equivalent to chronological for timezone-aware UTC.
            limit: If set, cap the number of returned rows.
        """
        params: list[object] = []
        sql = self._SELECT_ALL_BASE
        if since is not None:
            sql += " WHERE ts >= ?"
            params.append(since.isoformat())
        sql += " ORDER BY ts DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        cur = self._conn.execute(sql, params)
        return [self._row_to_memory(row) for row in cur.fetchall()]

    def get(self, trade_id: str) -> TradeMemory | None:
        """Fetch one row by ``trade_id`` or ``None`` if not present."""
        cur = self._conn.execute(self._SELECT_BY_ID, (trade_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_memory(row)

    def count(self) -> int:
        """Return the total number of stored memory rows."""
        cur = self._conn.execute("SELECT COUNT(*) FROM trade_memory")
        (n,) = cur.fetchone()
        return int(n)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection. Safe to call twice."""
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:  # pragma: no cover - already closed
            pass
