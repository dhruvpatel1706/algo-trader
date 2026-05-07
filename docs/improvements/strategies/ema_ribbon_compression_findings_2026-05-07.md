# EMA Ribbon Compression — Walk-Forward Findings (Daily Crypto)

**Run:** 2026-05-06T23:58 UTC
**Strategy:** `ema_ribbon_compression`
**Universe:** BTCUSDT, ETHUSDT, AVAXUSDT, LINKUSDT, LTCUSDT, BCHUSDT, DOGEUSDT (full-history subset of `crypto_majors`)
**Period:** 2022-01-01 → 2024-12-31 (1,096 daily bars per symbol)
**Walk-forward:** train_bars=252, test_bars=63

## Result: not promotable on daily bars

| Configuration | Trades | Win rate | Final equity | Profit factor |
|---|---:|---:|---:|---:|
| **Default** (Researcher spec: 0.5% spread, 5 bars, 0.8% breakout, ADX>=18) | **0** | — | $100,000 | — |
| Loose (1.0% spread, 3 bars, 0.5% breakout) | 7 | 28.6% | $100,058 | 1.05 |
| Very loose (1.5%, 3 bars, 0.3% breakout) | 17 | 29.4% | $98,232 | 0.58 |
| Relaxed ADX (1.5%, 3, 0.3%, ADX>=10) | 48 | 14.6% | $89,314 | 0.25 |
| Crypto-loose (2.0%, 3, 0.3%, ADX>=10) | 92 | 22.8% | $82,446 | 0.43 |

## Interpretation

The Researcher's spec produces **zero trades over 3 years** on the most liquid crypto majors. The strategy genuinely doesn't fire — daily crypto chop is too wide for a 0.5% EMA spread to materialise. Loosening just enough to fire trades pushes the win rate below 30% and profit factor below 1.0 (loose params barely break even at PF 1.05 with a 7-trade sample size that's not statistically meaningful).

The proposal in `docs/improvements/strategies/ema_ribbon_compression_breakout.md` explicitly recommended **4h bars** as the design timeframe. Daily is too coarse: by the time the ribbon compresses on daily, the structural move has typically completed.

## Recommendations

1. **Do NOT promote to live** at any of the tested parameter sets.
2. Keep the strategy in paper validation. It only fires under very specific conditions; the cumulative-cap risk gate prevents any meaningful loss exposure.
3. **Re-test on 4h bars** when the bars cache supports a second timeframe. The proposal's logic is sound; the timeframe matches what the researcher actually proposed.
4. Consider variants: shorter EMAs (5/8/13/21/34 instead of 8/13/21/34/55) might compress more often on daily.

## What this confirms

The walk-forward + promotion-gate pipeline (`src/backtest/promotion.py`) is doing exactly what it should: catching a strategy that LOOKS theoretically sound but has no measurable edge at the proposed configuration on the proposed asset class. This is the system working as designed.
