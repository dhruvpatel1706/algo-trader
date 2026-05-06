"""Recall episodic-memory hits for an in-flight signal evaluation.

This is the read-side counterpart of :mod:`src.memory.outcome_capture`.
Where outcome-capture persists what just happened, ``recall_for_signal``
pulls back the closest historical analogues so the autonomous reasoner
can be reminded of past wins and losses on similar setups before it
emits a judgment.

API surface (used by :mod:`src.agents.autonomous_reasoner`):

    >>> ctx = SignalContext(symbol="SPY", side="buy", ...)
    >>> hits = recall_for_signal_context(ctx, store=store, provider=provider, k=3)
    >>> lines = format_recalled_memories_for_prompt(hits)
    >>> for ln in lines: print(ln)
    SPY buy failed_breakout 2025-04-15: outcome=loss -1.0R; lesson: ...

Anchor-query design choice: we use ``"<strategy> <side> <symbol>"`` as the
core, optionally extended with regime + rule_confidence. This is short
enough to embed cheaply and stable enough that two runs of the same setup
hash to nearby vectors. We deliberately do NOT include real-valued fields
(entry_price, bar count) because tiny price drifts shouldn't change which
historical lessons surface.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.memory.recall import recall_similar

if TYPE_CHECKING:
    from src.agents.autonomous_reasoner import SignalContext
    from src.memory.embeddings import EmbeddingProvider
    from src.memory.models import TradeMemory
    from src.memory.store import MemoryStore

logger = logging.getLogger(__name__)


# Cap on how much of the recalled narrative we splice into the prompt
# bullet line. Keeps token cost bounded when multiple memories are recalled.
_NARRATIVE_PREVIEW_CHARS: int = 160


def build_anchor_query(ctx: SignalContext) -> str:
    """Build the anchor text used to embed-and-search the memory store.

    Format: ``"<strategy> <side> <symbol> regime=<regime> rule_conf=<conf>"``.
    The string is deterministic — the same SignalContext always produces
    the same anchor, which makes recall reproducible across calls and
    across runs.

    Symbols are kept REAL (not anonymized): the memory store contains
    real tickers, and we want the embedding to match SPY-failed_breakout
    setups against past SPY-failed_breakout setups. The anonymization
    happens later, when the recall hits are spliced into the LLM prompt.

    Args:
        ctx: Candidate signal that we're about to evaluate.

    Returns:
        A short, stable string suitable for embedding lookup.
    """
    regime = ctx.regime if ctx.regime is not None else "unknown"
    return (
        f"{ctx.strategy} {ctx.side} {ctx.symbol} "
        f"regime={regime} "
        f"rule_conf={round(ctx.rule_confidence, 2)}"
    )


def recall_for_signal_context(
    ctx: SignalContext,
    *,
    store: MemoryStore,
    provider: EmbeddingProvider,
    k: int = 3,
    min_similarity: float = 0.5,
    same_strategy_only: bool = False,
) -> list[tuple[TradeMemory, float]]:
    """Return the top-``k`` memories most similar to ``ctx``.

    Args:
        ctx: Candidate signal whose context drives the search.
        store: SQLite-backed memory store.
        provider: Embedding backend; must produce vectors of the same
            dimensionality as the stored rows.
        k: Maximum number of hits to return (after threshold filter).
        min_similarity: Drop hits whose cosine similarity is strictly
            below this. Default ``0.5`` filters obvious unrelated rows
            but keeps the bar low enough to surface lessons in a sparse
            store.
        same_strategy_only: If True, restrict recall to memories from
            the SAME strategy as ``ctx.strategy``. Useful when the
            caller wants tightly-related historical context only;
            defaults to False so cross-strategy lessons can still
            surface (e.g., a failed breakout reminds us about a similar
            failed range break).

    Returns:
        Up to ``k`` ``(memory, similarity)`` tuples sorted by descending
        similarity. May be shorter than ``k`` (or empty) if the store
        has fewer matching rows.
    """
    anchor = build_anchor_query(ctx)
    strategy_filter = ctx.strategy if same_strategy_only else None
    return recall_similar(
        anchor,
        store=store,
        provider=provider,
        k=k,
        min_similarity=min_similarity,
        strategy_filter=strategy_filter,
    )


def _format_outcome_for_line(mem: TradeMemory) -> str:
    """Render the realized outcome of a memory as a short tag.

    Format: ``"outcome=<label> <r>R"`` when both label and R are known;
    falls back to ``"outcome=<label>"`` or ``"outcome=open"`` otherwise.
    """
    label = mem.outcome_label or "open"
    if mem.outcome_r is None:
        return f"outcome={label}"
    sign = "+" if mem.outcome_r >= 0 else "-"
    return f"outcome={label} {sign}{abs(mem.outcome_r):.1f}R"


def _trim(text: str, max_chars: int = _NARRATIVE_PREVIEW_CHARS) -> str:
    """Collapse whitespace + cap length for a single-line preview."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def format_recalled_memories_for_prompt(
    memories: list[tuple[TradeMemory, float]],
) -> list[str]:
    """Render each ``(memory, similarity)`` pair as a single bullet line.

    Format (pinned for downstream consumers — DO NOT change without
    updating tests / docs)::

        <SYMBOL> <SIDE> <STRATEGY> <YYYY-MM-DD>: outcome=<label> <r>R; lesson: <narrative>.

    Args:
        memories: Output of :func:`recall_for_signal_context`. May be empty.

    Returns:
        A list of formatted lines, one per memory. Empty input yields an
        empty list — the caller decides whether to inject a placeholder
        message into the prompt or simply omit the field.
    """
    if not memories:
        return []
    lines: list[str] = []
    for mem, _sim in memories:
        date_iso = mem.ts.date().isoformat()
        outcome = _format_outcome_for_line(mem)
        lesson = _trim(mem.narrative)
        lines.append(
            f"{mem.symbol} {mem.side} {mem.strategy} {date_iso}: "
            f"{outcome}; lesson: {lesson}"
        )
    return lines


__all__ = [
    "build_anchor_query",
    "format_recalled_memories_for_prompt",
    "recall_for_signal_context",
]
