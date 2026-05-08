"""Strategy base class + Signal DTO."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    side: Literal["buy", "sell"]
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    confidence: float
    strategy_tag: str
    timestamp: datetime
    asset_class: Literal["equity", "option"] = "equity"
    notes: str = ""

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be in [0, 1]")
        if self.entry <= 0 or self.stop <= 0:
            raise ValueError("entry and stop must be positive")


class Strategy(ABC):
    """Abstract base. Subclasses implement universe() and generate_signals()."""

    name: str = "<unnamed>"
    params: Any = None

    # Bar interval the strategy expects in its ``generate_signals`` input.
    # The default ``"1d"`` matches what the daily bars cache feeds. Crypto
    # strategies that operate on intra-day timeframes (e.g.
    # :class:`~src.strategies.ema_ribbon_compression.EmaRibbonCompression`)
    # override this so the runtime fetches the right interval. Recognised
    # values match the ``interval`` arg of :func:`load_crypto_bars`:
    # ``"5m"``, ``"15m"``, ``"1h"``, ``"4h"``, ``"1d"``.
    bar_interval: str = "1d"

    @abstractmethod
    def universe(self) -> tuple[str, ...]:
        """Tickers this strategy operates on."""

    @abstractmethod
    def generate_signals(self, bars: dict[str, Any]) -> list[Signal]:
        """Convert per-symbol OHLCV frames into actionable Signals. Pure (no I/O)."""
