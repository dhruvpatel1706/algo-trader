"""wheel_etf — Cash-secured put wheel on SPY/QQQ. PAPER-ONLY EDUCATIONAL v1 stub.

Thesis: sell ~30-delta cash-secured puts (CSPs) on highly liquid index ETFs at
30-45 DTE, manage at 50% of max profit, gate entries by IVR>30 (sell when implied
vol is rich), and roll at 21 DTE to avoid the gamma spike. Goal: study premium-
selling dynamics on a paper account, not chase a P&L target.

Why this is a v1 stub
=====================
v1's backtest engine is long-only equities. A real CSP backtest needs:
  - historical options chain (Alpaca options data: 2024+)
  - per-strike greeks (to target ~0.30 delta)
  - IVR series (current IV vs trailing 252-day distribution)
  - assignment mechanics (CSP -> long shares -> covered-call leg)

Until those land, ``generate_signals()`` returns ``[]``. The :class:`WheelParams`
dataclass below is the design surface — change with care, and verify against
options-aware backtests before enabling.

Known failure modes (document these so future-me reads the warning sign)
========================================================================
- Vol shocks: a single Q1-2020 / Q4-2018-style move can wipe out months of premium.
- Assignment in a downtrend: collected premium does not save you from a -25% gap.
- Pin risk near expiration: gamma scales inversely with DTE.
- Bid-ask: option mid prints can be misleading; real fills are worse.
- Margin/PDT: in cash accounts, CSPs require full collateral; capital is locked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.strategies.base import Signal, Strategy


@dataclass(frozen=True, slots=True)
class WheelParams:
    """Cash-secured put wheel parameters.

    Each parameter has an economic rationale, a typical range, and a known
    failure mode. Don't widen ranges casually.
    """

    # Target option delta. ~0.30 delta puts finish ITM ~30% of the time, with
    # rich premium per DTE. Range: 0.20-0.35. Breaks: in compressed-vol regimes
    # (VIX<15) premium is too thin to compensate for assignment tail risk.
    target_delta: float = -0.30

    # Days-to-expiration window. 30-45 DTE balances theta decay against gamma
    # risk. Range: 30-60 DTE. Breaks: shorter = high theta + high gamma;
    # longer = capital tied up too long for the premium collected.
    dte_min: int = 30
    dte_max: int = 45

    # Manage at 50% of max profit. Closes the trade with most of the theta
    # captured, freeing capital and removing tail risk. Range: 30%-70%.
    # Breaks: too aggressive (>70%) leaves little theta; too conservative gives back.
    profit_target_pct: float = 0.50

    # IV-rank gate. IVR>30 = current IV >= 30th percentile of trailing 252d.
    # Selling premium when IV is elevated has positive expectancy. Range: 25-50.
    # Breaks: in compressed-IV regimes, no setups for weeks; sit on hands.
    ivr_min: float = 30.0

    # Roll at 21 DTE to avoid gamma spike on in-the-money puts. Range: 14-28.
    # Breaks: waiting longer can let an ITM short option's gamma blow up.
    dte_roll: int = 21


class WheelEtf(Strategy):
    """CSP wheel on SPY/QQQ. v1 emits no signals — see module docstring."""

    name = "wheel_etf"
    params = WheelParams()

    def universe(self) -> tuple[str, ...]:
        return ("SPY", "QQQ")

    def generate_signals(self, bars: dict[str, Any]) -> list[Signal]:
        # v1 stub: no signals until options chain + IVR + Greeks land in src/data/.
        # When enabling, validate against an options-aware engine first.
        return []
