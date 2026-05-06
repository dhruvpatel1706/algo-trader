# Live-Readiness Gate (Phase 9)

This document describes the gate that controls when (or if) a strategy can be
promoted from paper to live. It exists because v1 is paper-only by design, and
flipping `LIVE_TRADING=1` is a coordinated PR — not a runtime knob. Before that
PR can be merged, every strategy that will run live MUST satisfy all 9 criteria
below. The companion script `scripts/check_live_ready.py` automates the audit
so the operator can check status weekly without recomputing it by hand.

The script is **read-only**. It never modifies state, never writes to the
journal, never sets flags. It always exits with code 0. It is a report, not a
CI failure.

## Quick start

```bash
# audit one strategy
uv run python scripts/check_live_ready.py --strategy failed_breakout

# audit every strategy seen in the journal
uv run python scripts/check_live_ready.py --strategy all

# include backtest comparisons (criteria 2/3/7)
uv run python scripts/check_live_ready.py \
    --strategy all \
    --backtest-summary backtests/baseline_metrics.json

# JSON output, e.g. for piping to jq
uv run python scripts/check_live_ready.py --strategy all --json
```

The script writes a per-strategy detailed JSON to `live/runtime/`:
`live/runtime/live_ready_<strategy>_<YYYY-MM-DD>.json`.

## The 9 criteria

| #  | Criterion                              | Threshold                              |
| -- | -------------------------------------- | -------------------------------------- |
| 1  | Forward paper duration                 | >= 180 days from first journaled trade |
| 2  | Live Sharpe vs backtest                | live_sharpe / backtest_sharpe >= 0.7   |
| 3  | Live max drawdown vs backtest          | live_dd / backtest_dd <= 1.3           |
| 4  | Total trades (per strategy)            | >= 150                                 |
| 5  | Slippage MAE (live vs intended price)  | <= 5 bps                               |
| 6  | Risk-cap breaches in last 90 days      | 0                                      |
| 7  | Coherence (live_WR / backtest_WR, 30d) | >= 0.5                                 |
| 8  | Drift detector halts in last 30 days   | 0                                      |
| 9  | Pairwise correlation w/ other live     | <= 0.7                                 |

### 1. Forward paper duration

**Threshold:** at least 180 days since the strategy's first journaled event.

**Why:** Six months covers a typical regime shift (trend-up, range-bound,
correction). Anything shorter is a sample of one regime.

**How to fix if failing:** Wait. There is no shortcut. Backtests are not a
substitute for paper duration.

### 2. Live Sharpe vs backtest

**Threshold:** `live_sharpe >= 0.7 * backtest_sharpe`. Live Sharpe is computed
from realized PnL on closing fills journaled with `realized_pnl`,
annualized as `mean / std * sqrt(252)`.

**Why:** Backtests routinely overfit, but a healthy strategy keeps at least
70% of its backtest edge in production. Below that, the edge is suspect.

**Indeterminate when:** No `--backtest-summary` was supplied, or fewer than
two days of realized PnL exist in the journal.

**How to fix if failing:** Either the backtest was overfit (re-tune with
walk-forward + transaction-cost realism), or live conditions diverged
(diagnose with the per-day attribution before promoting).

### 3. Live max drawdown vs backtest

**Threshold:** `live_dd / backtest_dd <= 1.3`. Live DD is computed on the
cumulative-PnL equity curve from journaled `realized_pnl`.

**Why:** Live DD typically exceeds backtest DD because of unmodeled costs
and execution friction. A 30% buffer absorbs that. Beyond it, risk sizing is
likely too aggressive.

**Indeterminate when:** No `--backtest-summary`, or the equity curve never
reaches a positive peak (so a relative DD is undefined).

**How to fix if failing:** Reduce position size (lower `MAX_PER_TRADE_RISK`)
or tighten stops. Re-validate Sharpe afterwards.

### 4. Total trades (per strategy)

**Threshold:** >= 150 fills (full or partial) over the strategy's lifetime
in the journal.

**Why:** Statistical noise dominates below ~100 trades. 150 is a buffer.

**How to fix if failing:** Wait, or relax entry filters (carefully — don't
chase volume by lowering quality).

### 5. Slippage MAE

**Threshold:** Mean absolute slippage <= 5 bps. Computed from fills where
both `fill_price` and `intended_price` are journaled.

**Why:** 5 bps is a conservative cushion that won't eat the per-trade edge
of any strategy in this system.

**Indeterminate when:** Fills do not journal `intended_price` — fix
`src/execution/orders.py` to write it.

**How to fix if failing:** Use limit orders (don't chase), trade more liquid
symbols, avoid the open / close minutes.

### 6. Risk-cap breaches (last 90 days)

**Threshold:** 0 events with `event="cap_breach_alert"` for this strategy in
the last 90 days.

**Important distinction:** A *breach* is a violation that should NOT have
happened — e.g. a trade got submitted that exceeded a risk cap. A *refusal*
is the cap doing its job (a trade was correctly blocked) and is healthy.
This criterion only counts breaches.

**Why:** A breach means the risk infrastructure has a hole. Until that hole
is found and closed, going live amplifies the risk of an incident.

**How to fix if failing:** Investigate the breach (audit log in
`live/runtime/`), patch the hole, then wait 90 days clean.

### 7. Coherence (live vs backtest win rate)

**Threshold:** `live_win_rate (last 30 days) / backtest_win_rate >= 0.5`.

**Why:** Win rate degrades faster than Sharpe under regime change because it's
a binary outcome at every trade. A win-rate collapse is the early-warning sign
that the alpha has dried up.

**Indeterminate when:** No `--backtest-summary`, or no trades with realized
PnL in the last 30 days.

**How to fix if failing:** This usually means alpha decay. Re-validate the
backtest's assumptions; consider pausing the strategy.

### 8. Drift halts (last 30 days)

**Threshold:** 0 events with `event="drift_halt"` for this strategy in the
last 30 days. The drift detector is a planned Phase-8 ML component; today it
emits no events, so this criterion is effectively vacuous and will pass for
all strategies that have not been pre-emptively flagged.

**Why:** Repeated drift halts mean the live distribution has moved away from
the training distribution. Promoting in that condition is asking for a
left-tail event.

**How to fix if failing:** Retrain or retire. Don't dismiss a halt without
investigating the cause.

### 9. Pairwise correlation with other live strategies

**Threshold:** Maximum |Pearson correlation| of daily realized PnL between
this strategy and any other live strategy is <= 0.7. The lookback window is
63 trading days.

**Why:** Highly correlated strategies are effectively one larger strategy
sharing one larger risk. The portfolio risk caps assume diversification.

**Indeterminate when:** Only one strategy is live, or the other live
strategies do not have enough overlapping daily-PnL data.

**How to fix if failing:** Don't run them concurrently. Choose one. Or
modify entry rules so they take different signals.

## Backtest-summary file format

Criteria 2, 3, and 7 require backtest baselines. Supply them via JSON:

```json
{
  "failed_breakout": {
    "sharpe":    1.20,
    "max_dd":    0.12,
    "win_rate":  0.55,
    "n_trades":  312
  },
  "ma_pullback_trend": {
    "sharpe":    0.95,
    "max_dd":    0.18,
    "win_rate":  0.52,
    "n_trades":  410
  }
}
```

Path is passed via `--backtest-summary`. If the file is missing or a
strategy's entry is missing, the relevant criteria report INDETERMINATE
(neither PASS nor FAIL).

Maintain this file by re-running the backtest CLI after any strategy change
and pasting the new metrics in. There is no canonical generation tool yet.

## Cadence

Run weekly during forward paper validation. Spot-check daily once a strategy
is within ~30 days of the 180-day duration mark.

The script's per-strategy report at
`live/runtime/live_ready_<strategy>_<date>.json` is small (a few KB). Check
it in or archive it however you handle other journal-derived artifacts; do
not commit it to git history.

## What NOT to do

- **Do not** hand-wave a FAIL. If a criterion fails, fix the root cause or
  wait. Promoting with a known FAIL is a violation of the gate's purpose.
- **Do not** set `LIVE_TRADING=1` outside a coordinated PR. The PR is the
  audit checkpoint where the per-strategy reports get reviewed.
- **Do not** treat INDETERMINATE as PASS. INDETERMINATE means the data
  needed to evaluate the criterion does not exist yet. Get that data first
  (e.g. supply the backtest summary, journal the missing fields, wait for
  trades to accumulate).
- **Do not** lower thresholds to make the report pass. The thresholds are
  conservative on purpose. If a threshold genuinely needs revisiting, that's
  a separate discussion with explicit rationale, NOT a quiet edit to the
  constants in `scripts/check_live_ready.py`.
