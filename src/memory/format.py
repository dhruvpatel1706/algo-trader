"""Render recalled memories into a markdown block suitable for prompt context.

The output is one short paragraph per memory: symbol + side + entry
date + similarity, then the strategy name and a trimmed narrative,
followed by the realized outcome ("WIN +1.4R ($340)" / "LOSS ..." /
"OPEN"). Keeping every line short prevents the LLM context budget from
ballooning when k > 5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.memory.models import TradeMemory


_NARRATIVE_PREVIEW_CHARS: int = 220
_HEADER: str = "## Similar past trades:"


def _format_outcome(mem: TradeMemory) -> str:
    """Render the outcome of a memory as ``WIN +1.4R ($340)`` / ``OPEN`` / ...

    Falls back to ``OPEN`` when no label is present.
    """
    label = (mem.outcome_label or "open").upper()
    if mem.outcome_pnl_usd is None or mem.outcome_r is None:
        return label
    pnl_sign = "+" if mem.outcome_pnl_usd >= 0 else "-"
    r_sign = "+" if mem.outcome_r >= 0 else "-"
    return (
        f"{label} {r_sign}{abs(mem.outcome_r):.1f}R "
        f"({pnl_sign}${abs(mem.outcome_pnl_usd):.0f})"
    )


def _trim_narrative(text: str, max_chars: int = _NARRATIVE_PREVIEW_CHARS) -> str:
    """Single-line, length-capped narrative excerpt.

    Newlines get collapsed to spaces so the rendered block stays
    visually compact.
    """
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def format_memories_for_prompt(
    mems: list[tuple[TradeMemory, float]],
    *,
    max_items: int = 5,
) -> str:
    """Render recalled ``(memory, similarity)`` pairs as a markdown block.

    Args:
        mems: Output of :func:`recall_similar` (already sorted by
            descending similarity).
        max_items: Truncate the rendered list at this many lines. Set
            higher than the recall ``k`` if you want the full list,
            lower if you want to compress further.

    Returns:
        A markdown string starting with ``## Similar past trades:`` and
        followed by one numbered line per memory. If ``mems`` is empty,
        returns a placeholder message so the surrounding prompt
        structure stays consistent.
    """
    if not mems:
        return f"{_HEADER}\nNo similar prior trades in memory."

    lines: list[str] = [_HEADER]
    for idx, (mem, sim) in enumerate(mems[:max_items], start=1):
        date_iso = mem.ts.date().isoformat()
        outcome = _format_outcome(mem)
        narrative = _trim_narrative(mem.narrative)
        lines.append(
            f"{idx}. [{mem.symbol} {mem.side} {date_iso}, "
            f"similarity {sim:.2f}] {mem.strategy} — "
            f"{narrative}; outcome: {outcome}"
        )
    return "\n".join(lines)
