"""Wire closed-trade data through post-mortem + embed + persist.

Construction site of one :class:`OutcomeCapture` lives at runner startup.
It is then called by the broker's fill / exit handler each time a position
closes::

    >>> capture = OutcomeCapture(store=memory_store, provider=embedding_provider)
    >>> capture.record(ClosedTrade(...))      # synchronous, idempotent

Failure model: EVERY step is best-effort. A post-mortem failure falls back
to a mechanical summary; an embed failure stores the trade with an empty
embedding (still searchable by symbol/strategy SQL); a store failure logs
and returns ``None`` rather than crashing the runner. The mechanical PnL
data is the canonical record — the post-mortem lesson is enrichment.

Idempotency: a second :meth:`record` call with the same ``trade_id`` is a
no-op (logs a warning and returns the existing :class:`TradeMemory`). This
matters because broker fill events can be replayed in recovery scenarios
and the runner shouldn't duplicate memory rows.
"""

from __future__ import annotations

import logging

from src.memory.embeddings import EmbeddingProvider
from src.memory.models import TradeMemory
from src.memory.post_mortem import (
    ClosedTrade,
    _mechanical_fallback,
    classify_label,
    generate_post_mortem,
)
from src.memory.store import MemoryStore, MemoryStoreError

logger = logging.getLogger(__name__)


class OutcomeCapture:
    """Pipeline closed trades into the trade-memory store.

    The instance is stateless apart from holding refs to the store and
    embedding provider, so it's safe to share across threads (the
    underlying SQLite connection in :class:`MemoryStore` is configured
    in autocommit/WAL mode).
    """

    def __init__(self, store: MemoryStore, provider: EmbeddingProvider) -> None:
        """Hold refs to the store and embedding provider.

        Args:
            store: Where to persist the resulting :class:`TradeMemory`.
            provider: Backend used to embed the post-mortem narrative.
        """
        self._store: MemoryStore = store
        self._provider: EmbeddingProvider = provider

    def record(self, trade: ClosedTrade) -> TradeMemory | None:
        """Generate post-mortem, embed, store; return the persisted row.

        Idempotency: if ``trade.trade_id`` already exists in the store,
        log a warning and return the existing row without re-embedding.

        Best-effort: a failure in any sub-step is logged and the next
        step proceeds with a safe default. A failure in the FINAL store
        write returns ``None`` — but never raises into the caller.

        Args:
            trade: Closed-trade record to ingest.

        Returns:
            The stored :class:`TradeMemory`, or ``None`` if the store
            write failed. Callers may inspect the return for confirmation
            but should not depend on it for correctness.
        """
        # Step 1: idempotency check.
        try:
            existing = self._store.get(trade.trade_id)
        except Exception as e:
            # SQLite read failure is unexpected but not fatal — log and proceed
            # to the write path; a duplicate insert will fail loudly there.
            logger.warning(
                "outcome_capture: idempotency lookup failed for trade_id=%s: %s",
                trade.trade_id,
                e,
            )
            existing = None
        if existing is not None:
            logger.warning(
                "outcome_capture: trade_id=%s already in memory; skipping (idempotent)",
                trade.trade_id,
            )
            return existing

        # Step 2: post-mortem narrative. generate_post_mortem is itself
        # fail-safe (returns a mechanical fallback on any exception), so
        # this should not raise. We still wrap defensively.
        try:
            mortem = generate_post_mortem(trade)
            narrative = mortem.narrative
            label = mortem.label
        except Exception as e:
            logger.exception(
                "outcome_capture: post-mortem failed (%s); using mechanical",
                type(e).__name__,
            )
            label = classify_label(trade.pnl_usd)
            narrative = _mechanical_fallback(trade, label=label)

        # Step 3: embed the narrative. If the embedding backend fails (e.g.
        # OpenAI rate limit), persist the row anyway with an empty vector —
        # callers can still recall by symbol/strategy SQL even without it,
        # and a re-embed pass can backfill later.
        embedding: list[float] = []
        try:
            embedding = self._provider.embed(narrative)
        except Exception as e:
            logger.warning(
                "outcome_capture: embedding failed for trade_id=%s, persisting empty: %s",
                trade.trade_id,
                e,
            )

        # Step 4: build and persist the memory row.
        memory = TradeMemory(
            trade_id=trade.trade_id,
            ts=trade.entry_ts,
            symbol=trade.symbol,
            side=trade.side,
            strategy=trade.strategy,
            narrative=narrative,
            outcome_pnl_usd=trade.pnl_usd,
            outcome_r=trade.pnl_r,
            outcome_label=label,
            embedding=embedding,
        )
        try:
            self._store.add(memory)
        except MemoryStoreError as e:
            logger.error(
                "outcome_capture: store write failed for trade_id=%s: %s",
                trade.trade_id,
                e,
            )
            return None
        except Exception as e:
            logger.exception(
                "outcome_capture: unexpected store error for trade_id=%s (%s)",
                trade.trade_id,
                type(e).__name__,
            )
            return None
        return memory


__all__ = ["OutcomeCapture"]
