# Overnight Strategy Validation — 2026-05-07

Walk-forward backtests run during the overnight session against
`crypto_majors` daily bars 2022-01-01 → 2024-12-31 (1,096 bars × 7 symbols
with full history: BTC, ETH, AVAX, LINK, LTC, BCH, DOGE).

Walk-forward window: train_bars=252, test_bars=63, n_windows=13.

## Result table

| Strategy | Trades | Return | Sharpe | Sortino | PF | Win rate | Max DD | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `ma_pullback_trend_crypto` | 47 | +33.4% | 0.97 | 1.32 | 1.60 | 48.9% | 16.6% | **promotable** |
| `failed_breakout_crypto` | 16 | +9.5% | 0.74 | 1.01 | 1.94 | 56.2% | 4.9% | **needs more trades** |
| `ema_ribbon_compression` (default) | 0 | 0% | — | — | — | — | — | **not promotable on daily** |
| `ema_ribbon_compression` (loose) | 7 | +0.06% | — | — | 1.05 | 28.6% | — | **not promotable** |

`funding_rate_divergence`: cannot backtest. The funding-rate APIs (Binance,
Bybit, OKX) only return ~100 most-recent records with no deep historical
window for free queries. Strategy is paper-only until either (a) we
buffer funding history for several months in our own DB, or (b) we
purchase a paid endpoint.

## Promotion-gate evaluation

Per `docs/improvements/strategies/*.md` and the policy in
`src/backtest/promotion.py`:

| Gate | `ma_pullback_trend_crypto` | `failed_breakout_crypto` |
|---|---|---|
| n_trades ≥ 30 | 47 ✓ | 16 ✗ (extend period) |
| profit_factor > 1.2 (after fees) | 1.60 ✓ | 1.94 ✓ |
| Sharpe > 0.7 | 0.97 ✓ | 0.74 ✓ |
| max_dd ≤ 20% | 16.6% ✓ | 4.9% ✓ |
| OOS Sharpe degradation < 50% | per-window 0.53 vs joined 0.97 — borderline | per-window 0.34 vs joined 0.74 — borderline |
| Single trade ≤ 20% of P&L | — (would need trade-by-trade audit) | — |

**Recommendation:** `ma_pullback_trend_crypto` looks ready to advance
through Phase 9 gating into the live ladder once the per-window
stability concern is investigated (Sharpe drops from 0.97 joined to
0.53 per-window — that's a high standard deviation across windows that
could indicate regime sensitivity). `failed_breakout_crypto` needs more
trades; rerunning the backtest from 2020 should clear the n_trades gate.

## EMA Ribbon Compression — what to do

The strategy is theoretically sound but produces zero trades on daily
crypto bars at the Researcher's default parameters. The proposal
recommended **4h bars**; daily is too coarse for the compression
condition to set up (EMA-55 lag washes out faster than the 5-bar
compression window can confirm).

Action: keep the strategy in paper validation. It will not generate
false signals (or any signals, on daily data). Re-test on 4h bars when
the bars cache supports a second timeframe.

Alternative parameter exploration tested:
- 1.0% spread + 3 bars + 0.5% breakout: 7 trades, PF 1.05 — break-even, sample too small
- 1.5% spread + 3 bars + 0.3% breakout: 17 trades, PF 0.58 — losing money
- Relaxed ADX to 10: 48 trades, PF 0.25 — much worse

Loosening parameters does NOT help; the strategy is fundamentally
mistuned for the timeframe.

## Funding Rate Divergence — what's blocking validation

Public funding APIs are rate-limited to ~100 records. We need:
1. Run our own funding-history buffer (script that polls every 8h and
   appends to a parquet file). After 6 months we have a meaningful
   sample.
2. OR buy a Polygon/Coinmetrics-style historical endpoint.

Strategy is shipped with full unit-test coverage of the rule logic, but
real-world edge is unproven.

## Open questions for the operator

1. The `ma_pullback_trend_crypto` per-window Sharpe std of 2.90 is
   very high — does this look like regime sensitivity? Worth a closer
   audit before promoting to live.
2. The 2022 BTC bear market (-65%) may dominate the joined return.
   Need to slice 2023-2024 separately to confirm the strategy works
   in non-bear regimes.
3. Failed breakout has 1.94 PF but only 16 trades over 3 years — that's
   ~5 trades/year. Is the strategy too picky to provide meaningful
   diversification?

---

## REGIME-SENSITIVITY UPDATE (added 04:45 UTC)

After running the same strategies on a 5-year window (2020-2024,
including the 2020-2021 BTC bull market), the picture changes
substantially. Both strategies' edge collapses when the period
includes strong trending regimes:

| Period | `ma_pullback_trend_crypto` | `failed_breakout_crypto` |
|---|---|---|
| **2020-2024** (5yr — incl. bull) | 55 trades, **Sharpe 0.30, PF 0.99** (break-even) | 27 trades, **Sharpe -0.20, PF 0.73** (losing) |
| **2022-2024** (3yr — chop) | 38 trades, Sharpe 0.97, PF 1.38 | 11 trades, Sharpe 0.89, PF 1.86 |
| **2024** (1yr) | 2 trades (too few) | 0 trades |

**Interpretation:** Both strategies are regime-sensitive mean-reversion
plays. They work in choppy/ranging markets (2022-2024 was bear→grind
with frequent reversals) and **lose money during strong trending
regimes** (2020-2021 was a near-vertical BTC bull, where buying
pullbacks against the trend or fading breakouts both lose).

**This significantly tempers the earlier "promotable" recommendation.**
The 2022-2024 PF 1.60 and Sharpe 0.97 for `ma_pullback_trend_crypto`
were flattered by the regime, not by the strategy's intrinsic edge.
Across the full cycle the strategy is essentially break-even.

**Live-deployment implications:**
1. **Do NOT promote either strategy to a real-capital ladder based on
   the 2022-2024 numbers alone.** The promotion gate's correlation
   /coherence rules will likely catch this once we have 6 months of
   forward paper, but the operator should know now.
2. **Add a regime filter.** Before signaling, check whether we're in a
   chop regime (e.g. ADX < 25 across the universe, or weekly returns
   bounded ±5% for 8+ weeks). Suppress signals during strong trends
   where these mean-reversion plays will lose.
3. **Current regime per researcher session 2:** "downtrend /
   capitulation compression" — the regime where these strategies
   should perform well. Short-term paper performance should be
   informative; long-term cycle robustness still requires regime
   filtering.

**The 2024-only test is revealing:** only 2 trades for
`ma_pullback_trend_crypto` and 0 for `failed_breakout_crypto`. The
strategies are too picky to fire often in the calmer 2024 regime.
This raises a separate concern: are they providing enough
diversification to be worth the operational overhead?
