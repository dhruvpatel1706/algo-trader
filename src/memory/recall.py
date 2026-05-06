"""Top-k cosine recall over the SQLite-backed :class:`MemoryStore`.

Embeddings written by this package are L2-normalized, so cosine
similarity collapses to a single dot product. We do a linear scan in
Python — fine up to a few thousand rows; if we ever exceed that we
should swap in ``sqlite-vec`` or ``faiss``, but for v1 the simplicity
is worth the cycles.

The recall API takes a *narrative* string (not a pre-computed vector)
because callers shouldn't need to know which provider is configured;
the function embeds the query with the same provider used for writes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.memory.embeddings import EmbeddingProvider
    from src.memory.models import TradeMemory
    from src.memory.store import MemoryStore


class DimensionMismatchError(ValueError):
    """Raised when the query embedding's dimensionality differs from store rows."""


def cosine(a: list[float], b: list[float]) -> float:
    """Return the cosine similarity of two vectors.

    Vectors of different length raise :class:`DimensionMismatchError`.
    A zero-norm vector yields ``0.0`` (rather than NaN); this matches
    what callers typically want for a "no match" sentinel.
    """
    if len(a) != len(b):
        raise DimensionMismatchError(
            f"cosine: dimension mismatch len(a)={len(a)} len(b)={len(b)}"
        )
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / ((norm_a**0.5) * (norm_b**0.5))


def recall_similar(
    narrative: str,
    *,
    store: MemoryStore,
    provider: EmbeddingProvider,
    k: int = 5,
    since_days: int | None = 365,
    min_similarity: float = 0.0,
    strategy_filter: str | None = None,
) -> list[tuple[TradeMemory, float]]:
    """Return the top-``k`` past memories most similar to ``narrative``.

    Args:
        narrative: Free-form description of the current setup; embedded
            with ``provider`` to produce the query vector.
        store: The persistence handle to scan.
        provider: Embedding backend. Must produce vectors with the same
            dim as the stored rows; a mismatch raises
            :class:`DimensionMismatchError`.
        k: Maximum number of results to return.
        since_days: If set (default 365), exclude rows older than this
            many days. Pass ``None`` to disable the time filter.
        min_similarity: Drop rows whose cosine similarity is strictly
            below this threshold. Default ``0.0`` accepts everything.
        strategy_filter: If set, only consider rows whose ``strategy``
            field equals this string.

    Returns:
        A list of ``(memory, similarity)`` tuples sorted by descending
        similarity. Length is at most ``k``; may be shorter (or empty)
        when fewer rows pass the filters / threshold.

    Raises:
        DimensionMismatchError: If any candidate row's embedding has a
            different length than the query embedding.
    """
    query_vec = provider.embed(narrative)
    expected_dim = len(query_vec)
    if expected_dim != provider.dim:
        # Defensive — a misbehaving provider shouldn't poison recall.
        raise DimensionMismatchError(
            f"provider.embed() returned dim={expected_dim} but provider.dim={provider.dim}"
        )

    since: datetime | None = None
    if since_days is not None:
        since = datetime.now(UTC) - timedelta(days=since_days)

    candidates: list[TradeMemory] = store.all(since=since)

    scored: list[tuple[TradeMemory, float]] = []
    for mem in candidates:
        if strategy_filter is not None and mem.strategy != strategy_filter:
            continue
        if len(mem.embedding) != expected_dim:
            raise DimensionMismatchError(
                "stored embedding dim "
                f"({len(mem.embedding)}) != query dim ({expected_dim}) "
                f"for trade_id={mem.trade_id}"
            )
        sim = cosine(query_vec, mem.embedding)
        if sim < min_similarity:
            continue
        scored.append((mem, sim))

    # Sort descending by similarity. Stable sort preserves write-order
    # within ties, which makes recall reproducible in tests.
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]
