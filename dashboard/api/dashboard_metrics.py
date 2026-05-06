"""Compute trailing metrics from journal-recorded trades."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from dashboard.api.journal_reader import read_trades


def _trade_pnl(event: dict[str, Any]) -> float | None:
    """Best-effort pull of pnl from a fill or submit event. Returns None if unknown."""
    if "pnl" in event:
        try:
            return float(event["pnl"])
        except (TypeError, ValueError):
            return None
    return None


def _split_periods() -> dict[str, tuple[date, date]]:
    # JournalWriter keys files by UTC date; compare on UTC so a PT operator
    # at 9:30am doesn't see "today" empty for the first 8 hours each day.
    today = datetime.now(UTC).date()
    return {
        "30d": (today - timedelta(days=30), today),
        "90d": (today - timedelta(days=90), today),
        "365d": (today - timedelta(days=365), today),
    }


def trailing_metrics() -> dict[str, dict[str, float | int]]:
    """Compute per-period PnL aggregates from journal `event:fill`/`event:submit` records."""
    out: dict[str, dict[str, float | int]] = {}
    for label, (start, end) in _split_periods().items():
        events = read_trades(start=start, end=end)
        pnls = [p for e in events if (p := _trade_pnl(e)) is not None]
        wins = sum(p for p in pnls if p > 0)
        losses = -sum(p for p in pnls if p < 0)
        n = len(pnls)
        out[label] = {
            "n_trades": n,
            "win_rate": (sum(1 for p in pnls if p > 0) / n) if n else 0.0,
            "profit_factor": (wins / losses) if losses > 0 else (float("inf") if wins > 0 else 0.0),
            "expectancy": (sum(pnls) / n) if n else 0.0,
            "total_pnl": sum(pnls),
        }
    return out
