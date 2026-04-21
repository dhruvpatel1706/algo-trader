"""Slippage and commission models for the backtest engine.

Defaults reflect realistic retail-flow execution at Alpaca paper.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CostModel:
    """Per-fill cost parameters."""

    # ATR-proportional slippage. per-share slip = slip_atr_mult * ATR(period).
    slip_atr_mult: Decimal = Decimal("0.05")
    # Equity commission, $/share.
    equity_commission_per_share: Decimal = Decimal("0.005")
    # Options commission, $/contract.
    options_commission_per_contract: Decimal = Decimal("0.65")

    def slippage(self, atr: Decimal, side: str) -> Decimal:
        """Per-share slippage in dollars. Buys pay up; sells get hit."""
        slip = atr * self.slip_atr_mult
        return slip if side == "buy" else -slip

    def commission(self, qty: int, asset_class: str = "equity") -> Decimal:
        if asset_class == "equity":
            return self.equity_commission_per_share * qty
        if asset_class == "option":
            return self.options_commission_per_contract * qty
        raise ValueError(f"unknown asset_class: {asset_class}")


DEFAULT_COSTS = CostModel()
