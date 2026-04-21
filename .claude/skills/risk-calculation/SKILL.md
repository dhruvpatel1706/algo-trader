---
name: risk-calculation
description: Computes position size, portfolio heat, drawdown status, and Kelly fraction. Use whenever sizing, stop distance, ATR-based sizing, "how many shares", or "is this trade safe" comes up.
---

# Risk math (canonical helpers in `src/risk/`)

## Position size
```python
from decimal import Decimal
from src.risk.sizing import position_size
qty = position_size(
    equity=Decimal("100000"),
    risk_pct=Decimal("0.01"),
    entry=Decimal("478.25"),
    stop=Decimal("472.10"),
)
```
Formula: `qty = floor((equity * risk_pct) / abs(entry - stop))`. Capped to `floor((equity * MAX_SINGLE_POSITION) / entry)`.

## Portfolio heat
```python
from src.risk.sizing import portfolio_heat
heat = portfolio_heat(positions)  # sum of (open_risk_$ / equity) across open positions
```
Reject any add that pushes heat above `MAX_PORTFOLIO_HEAT` (default `0.06`).

## Kelly (use sparingly; prefer quarter-Kelly)
```python
from src.risk.sizing import kelly_fraction, quarter_kelly
f = quarter_kelly(win_rate=0.55, win_loss_ratio=1.4)
```

## Drawdown status
`(trailing_peak - current_equity) / trailing_peak`. Halt at `0.15`.

All functions are pure, unit-tested, and use `Decimal` for money.
