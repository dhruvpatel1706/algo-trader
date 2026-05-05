# Live readiness gate

## Overview

This is the gate between paper and real capital. A strategy graduates from forward-paper validation to a small real-money allocation only when **all 9 gates below pass simultaneously**, audited by `scripts/check_live_ready.py`. A single failing gate blocks promotion regardless of how strong the others look — the gates are designed so that any one of them being soft is enough evidence that the edge is not yet trustworthy with real capital.

## The 9 gates

| # | Gate | Threshold | Why it matters |
|---|------|-----------|----------------|
| 1 | Forward paper duration | ≥ 6 months | Six months is the minimum window to see the strategy survive a regime shift it did not see in backtest. |
| 2 | Live Sharpe vs backtest Sharpe | live ≥ 0.7 × backtest | Forward live Sharpe always degrades; a 30% haircut is the largest gap that still implies the edge is real. |
| 3 | Live max DD vs backtest max DD | live ≤ 1.3 × backtest | If realized drawdown blows past the modeled tail, the risk model is wrong and sizing is unsafe. |
| 4 | Trade count | ≥ 150 trades total | Fewer than ~150 fills means the win-rate / Sharpe estimates are dominated by noise, not signal. |
| 5 | Slippage MAE vs backtest assumption | ≤ 5 bps | If realized slippage exceeds modeled slippage by more than 5 bps the backtest P&L is structurally optimistic. |
| 6 | Risk-cap breaches in journal (90d) | 0 | Any `risk_check.decision == "REJECT"` in the journal means the strategy tried to violate a cap — graduating it would amplify that. |
| 7 | Coherence (live\_WR / backtest\_WR, last 30d) | ≥ 0.5 | If recent win-rate has fallen below half of what backtest implies, the regime has shifted and edge is decaying. |
| 8 | Drift detector halts (last 30d) | 0 | A drift halt is the system's own statement that the data is no longer the data the model was fit on. |
| 9 | Pairwise correlation with all live strategies | ≤ 0.7 | New live capital must add diversification, not concentrate exposure to an existing factor already in the live book. |

## How to run the checker

Audit a single strategy:

```bash
uv run python scripts/check_live_ready.py --strategy mr_etf
```

Audit the whole portfolio (every strategy in `backtests/`):

```bash
uv run python scripts/check_live_ready.py --portfolio
```

Useful flags:

- `--asof YYYY-MM-DD` — pin the audit to a fixed date instead of today (reproducible reports).
- `--json` — emit machine-readable JSON instead of a human report (CI / dashboards).

Exit code is `0` when every gate passes and `1` otherwise.

## The real-capital ladder

Promotion is **stepwise**, not a single jump from $0 to "real size":

1. **$1k** — initial real allocation. The strategy must run for a full 30 days at this size with no risk-cap breaches, no drift halts, and a positive Sharpe on the live tape before any increase.
2. **$2.5k** — after 30 clean days at $1k.
3. **$5k** — after 30 clean days at $2.5k, repeating the audit (all 9 gates must still pass).
4. **$10k** — after 30 clean days at $5k. Above $10k requires a fresh policy review, not a ladder step.

A failure at any rung resets the strategy to the previous rung. Two consecutive failures pull the strategy back to paper.

## The coordinated-PR rule

Going live for the first time, or moving a strategy off paper, is **not** a code-only or doc-only change. The PR must modify both of these files in one review:

- `docs/policy.md` — remove the paper-only language for the specific strategy and name the policy basis (account type, PDT status, etc.).
- `src/execution/broker.py` — replace the `LiveBroker` `NotImplementedError` stub (or its per-strategy equivalent) with a real implementation.

A PR that touches only one of those two files is rejected on review. This is the same rule already documented in `docs/policy.md`, repeated here so the gate-check audit and the deploy mechanic stay in sync.
