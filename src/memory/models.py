"""Dataclass for an episodic trade memory record.

An entry stores the human-readable narrative that captures the entry
rationale (what setup we saw, what regime, what catalyst) along with the
realized outcome once the trade closes. The embedding is computed from
the narrative string via an :class:`~src.memory.embeddings.EmbeddingProvider`
and stored alongside so cosine recall is a single SQL scan plus dot
product over the in-memory vector list.

The `outcome_*` fields are nullable to support the open-trade lifecycle:
write the row at entry with the embedding and `outcome_label="open"`,
then mutate via :meth:`MemoryStore.update_outcome` once the trade closes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True, frozen=True)
class TradeMemory:
    """One episodic trade memory.

    Attributes:
        trade_id: Stable identifier (ULID or uuid4 string). Primary key.
        ts: Entry timestamp; must be timezone-aware (UTC) for SQLite ISO
            round-trips to be unambiguous.
        symbol: Ticker symbol, e.g. ``"SPY"``.
        side: ``"buy"`` or ``"sell"``.
        strategy: Strategy name that produced the entry, e.g.
            ``"failed_breakout"``.
        narrative: Human-readable description of the setup. This is the
            string that gets embedded — it should be descriptive enough
            that two narratives describing the same kind of setup
            cluster together in embedding space.
        outcome_pnl_usd: Realized P&L in USD; ``None`` if still open.
        outcome_r: R-multiple of the trade; ``None`` if still open.
        outcome_label: One of ``"win"``, ``"loss"``, ``"breakeven"``,
            ``"open"``; ``None`` is permitted for backwards compat with
            partially populated rows.
        embedding: L2-normalized float vector. Must match the
            :attr:`EmbeddingProvider.dim` of the provider used at recall
            time, otherwise :class:`DimensionMismatchError` is raised.
    """

    trade_id: str
    ts: datetime
    symbol: str
    side: str
    strategy: str
    narrative: str
    outcome_pnl_usd: float | None
    outcome_r: float | None
    outcome_label: str | None
    embedding: list[float] = field(default_factory=list)
