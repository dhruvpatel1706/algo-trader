# Edge research extraction, May 2026

> **See also**: `docs/strategy_catalog.md` for a curated catalog of replicable academic + competition-vetted strategies (with honest "why it might not work for us" sections), `docs/cautionary_tales.md` for famous trading-system blow-ups mapped to our risk caps, and `docs/research_sources.md` for where to scout new strategy candidates beyond what's cataloged.

This note distills the user's YouTube transcripts, the Power of Stocks material, and current public data sources into testable bot work. It is research, not a live-trading permission slip. `docs/policy.md` remains the source of truth: this repo is paper-only until a reviewed policy and broker change lands.

## Ground truth

- The goal is not to let an LLM invent trades. The bot should run mechanical strategies, backtest them, paper trade them, and let an LLM help with summarization, sentiment, anomaly review, and kill/promote governance.
- `100 USD -> 2M USD` is not a realistic engineering target under sane risk caps. Treat it as a motivational fantasy, not a design requirement. The real design target is survival, measurable edge, compounding, and no account-ending behavior.
- "HFT" is out of scope for this repo. True HFT needs colocation, direct feeds, low-latency infrastructure, and fee/rebate engineering. The realistic version here is high-cadence paper trading on 1m/5m bars or 24/7 crypto polling with explicit slippage, fees, and kill switches.
- Copy-trading politicians and wallets is usable only as an alternative-data feature. It should not be used as blind execution because disclosures are delayed, wallet labels are survivorship-biased, and copied fills are usually worse than the original trader's fills.

## Power of Stocks signals worth testing

### 1. Failed breakout / liquidity-grab fade

Source idea: Power of Stocks positional gold transcript plus the Chad Trades ORB-failure transcript.

Tradable rule family:
- Mark a prior high/low, session high/low, previous day high/low, gap edge, or Donchian range boundary.
- Price breaks the level, fails to follow through, and quickly closes back inside the prior range.
- Enter opposite the failed breakout only after a rejection candle or close-back-inside confirmation.
- Stop goes beyond the failed wick or beyond the rejection level.
- Target is the origin of the move, the opposite range edge, or a fixed R multiple.

Bot version:
- Daily: Donchian 20/55 rejection with ATR stop.
- Intraday: previous day high/low or opening range rejection on 1m/5m bars.
- For gold/crypto: test on `GLD`, `GC=F`, `BTC-USD`, `ETH-USD` for research, then use broker-grade feeds before execution.

Acceptance gate:
- Minimum 100 trades for intraday, 30 for daily.
- Profit factor > 1.2 after fees/slippage.
- No single trade contributes more than 20% of total PnL.

### 2. 5 EMA mean-reversion scalp

Current public summaries describe the Power of Stocks 5 EMA strategy as a short-term mean-reversion setup where price stretches away from the 5 EMA and the trader waits for a qualifying candle/alert candle before taking the break back toward the mean.

Tradable rule family:
- Timeframe: originally 5m Nifty style, but testable on liquid ETFs/futures/crypto.
- For short setup: price is extended above 5 EMA, an alert candle forms, and the next candle breaks the alert candle low.
- For long setup: mirror condition below 5 EMA, then break above alert candle high.
- Stop: alert candle extreme.
- Target: at least 1R, ideally 2R-3R, or touch of 5 EMA.

Important Power of Stocks nuance:
- The edge is not "5 EMA magic". It is small stop, large reward, and sizing up only when the setup aligns with higher-timeframe levels.
- Do not optimize 4 EMA vs 5 EMA vs 6 EMA endlessly. Fix the rule first, then test robustness around it.

### 3. Gap as support/resistance

Source idea: Power of Stocks 5 EMA transcript.

Tradable rule family:
- Detect unfilled daily gaps.
- Treat the upper/lower gap edge as support/resistance.
- If price returns to the gap edge and the 5 EMA or 15 EMA setup aligns, allow higher confidence.
- If the level is ambiguous, reduce size rather than skipping every rule-valid trade.

Bot version:
- Build `gap_levels(symbol, lookback)` from daily bars.
- Intraday entries only when price is within `x * ATR_5m` of a gap edge.
- Use as a confluence multiplier, not a standalone signal.

### 4. Range shift and first pullback

Source idea: Power of Stocks "range shift" explanation.

Tradable rule family:
- Identify a prior range with clear support/resistance.
- A breakout or breakdown changes the range.
- Trade the first pullback to 5 EMA or 15 EMA in the new direction, especially if it retests the broken level.

Bot version:
- Use swing pivots or Donchian breakout to define range shift.
- Use EMA pullback for entry.
- Reject if price has already moved too far from the breakout origin, since late pullbacks often mean exhaustion.

### 5. Trend capture / pyramiding only after proof

Source idea: Power of Stocks positional gold transcript.

Tradable rule family:
- Initial trade starts with defined loss.
- Add only after the trade is already in profit and a second valid setup appears.
- Do not add just because unrealized PnL is green.
- Trail to break-even or structure once the trade has moved enough.

Bot version:
- Pyramiding is disabled by default.
- Enable only in paper after a strategy has a live-paper profit buffer.
- Add size must not increase total portfolio heat beyond policy caps.

### 6. Traffic Light strategy

Public summaries describe a multi-EMA/Bollinger framework using 3/5/8/13/21 EMAs plus Bollinger Bands, with trend context from 20/200 EMAs.

Use as:
- A regime classifier, not a standalone alpha candidate at first.
- "Green": price above long/medium trend filters.
- "Red": price below them.
- "Dot/pullback": no new trade unless another primary setup fires.

Reason:
- Public backtest chatter around Traffic Light is mixed, and it is likely overfit if used as a direct entry engine.

## Public alternative-data sources

### Congressional trades

Reality:
- House/Senate trades are public, but the STOCK Act allows reporting up to 45 calendar days after the transaction, and disclosures use broad dollar ranges. This is too delayed for direct copy-trading.

Sources:
- House official disclosures: https://disclosures-clerk.house.gov/PublicDisclosure
- House 2026 instruction guide confirms PTRs over $1,000 and the 30-days-aware / 45-days-transaction deadline: https://ethics.house.gov/wp-content/uploads/2026/04/2025-Published-Instruction-Guide-4-15-2026-1.pdf
- Senate official disclosure search: https://efdsearch.senate.gov/
- Quiver API for structured data: https://www.quiverquant.com/api-setup/
- CongressFlow explanation of limitations: https://congressflow.com/learn/how-to-track-congressional-trades
- Capitol Trades is useful for browsing, but public API access is limited/non-documented.

Bot use:
- Do not auto-copy.
- Convert to a slow thematic feature: sector/ticker accumulation, committee relevance, cluster trades, late-filer flag.
- Use only as a filter or watchlist boost, then require price-action confirmation.

### SEC insider transactions

Reality:
- SEC Form 4 is more timely than congressional data. Corporate insiders usually must report changes quickly, and the SEC APIs update throughout the day.

Sources:
- SEC EDGAR APIs: https://www.sec.gov/edgar/sec-api-documentation
- OpenInsider for browsing: http://openinsider.com/
- Quiver insider endpoints if using one paid aggregator.

Bot use:
- Track open-market buys, not grants or automatic sales.
- Score higher when multiple insiders buy in a short window.
- Exclude microcaps and illiquid tickers unless explicitly researching a microcap strategy.
- Use filing date, not transaction date, in backtests to avoid look-ahead.

### Crypto wallets / smart money

Reality:
- On-chain data is faster than politician disclosures, but "95% win-rate wallet" claims are usually survivorship-biased. Many wallets win on illiquid tokens, private allocations, airdrops, MEV, or fills that a copier cannot reproduce.

Sources:
- Nansen Smart Money API labels and endpoints: https://docs.nansen.ai/api/smart-money
- Arkham for entity/wallet intelligence: https://platform.arkhamintelligence.com/
- Etherscan API for raw Ethereum transfers/trades: https://docs.etherscan.io/
- DexScreener API for liquidity/price context: https://docs.dexscreener.com/api/reference
- Hyperliquid leaderboards/API for perp trader research: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api

Bot use:
- Shadow-copy first: record wallet trade, paper simulate our achievable fill, compare 30/90/180-day copy PnL.
- Reject wallets with fewer than 50 trades, low liquidity, high token concentration, or gains dominated by one token.
- Require our simulated slippage < 25 bps for majors, much stricter if trading size grows.
- For BTC/ETH, wallet flows are a sentiment/liquidity feature, not an entry by themselves.

## Asset-specific strategy shortlist

### Gold

- Failed breakout around prior high/low and gap levels.
- 20 EMA/200 EMA trend pullback.
- Breakout retest after London/NY session shift if intraday data is available.
- Instruments for research: `GLD`, `GC=F`, `XAUUSD` broker feed.

### Bonds

- Mean reversion in `TLT`, `IEF`, `AGG`, `BND`.
- Macro/event filter around CPI, FOMC, treasury auctions.
- Trend pullbacks when 20 SMA is rising/falling and price is on the correct side of 200 SMA.
- Avoid high-frequency bond ETF scalping on free data. The edge is macro/regime, not speed.

### BTC / ETH

- 24/7 trend pullbacks on 1h/4h bars.
- Failed breakout of prior day/week high/low.
- Funding/open-interest/liquidation features if using a crypto data provider.
- Smart-wallet and exchange-flow features as filters.
- Strict fee and slippage modeling. Small accounts get eaten alive by overtrading.

### US equities / ETFs

- Current `mr_etf` remains the low-trend mean-reversion baseline.
- Add trend-pullback counterpart.
- Add failed-breakout rejection.
- Add insider/congress/news as filters, not direct entries.

## LLM/Claude use

Good uses:
- Convert transcripts and research into rule specs.
- Summarize filings/news.
- Score sentiment after ticker anonymization.
- Review backtest artifacts for suspicious overfit patterns.
- Governance: kill/promote recommendations based on already-computed metrics.

Bad uses:
- "Find me a strategy that makes money."
- Direct execution decisions without deterministic rules.
- Backtest math in prose.
- Any live action that bypasses risk/compliance.

## Implementation order

1. Add a universe/source registry for assets and alternative-data feeds.
2. Implement `williams_vix_fix`, `ema`, gap-level detection, and range-shift helpers.
3. Implement daily strategies first: failed breakout, MA pullback, gap reversion.
4. Add intraday data loader only after choosing and paying for a feed with reliable 1m bars.
5. Add alternative-data ingestion: SEC Form 4 first, Quiver/congress second, crypto wallet shadow-copy third.
6. Build promotion gates before paper deployment: trade count, out-of-sample degradation, correlation, drawdown, and live/backtest coherence.
7. Only after 30-90 days of shadow/paper evidence, allow any strategy to influence position size.

## Walk-forward results, May 2026

The first walk-forward evaluation of the two Codex-implemented strategies against the broader (universe-loader-resolved) ticker set covering 2018-2025. Per the charter: results determine promotion status; rules are NOT tuned to push borderline metrics over the line.

### `failed_breakout` (2018-2025)

| Metric | Value | Promotion gate |
|---|---|---|
| Sharpe (joined) | 0.61 | n/a (no hard floor) |
| Profit factor | 1.04 | **fail** (≥1.2) |
| Max DD | 20.4% | **fail** (≤20%) |
| n_trades | 337 | pass (≥30) |
| Win rate | 40.7% | n/a |
| Per-window Sharpe std/mean | 1.68 | **fail** (≤0.5) |
| Total return | 45.7% / 8y | ~4.8%/yr |

**Status: research-only.** Profit factor barely positive after costs. Per-window stability fails — strategy works in some regimes, bleeds in others. Plausible improvements (DO NOT implement without OOS validation): tighten WVF threshold, add gap_levels confluence (Phase 2a), add news_filter once Phase 5 lands. Re-evaluate in Phase 2a as a confluence consumer rather than standalone.

### `ma_pullback_trend` (2018-2025)

| Metric | Value | Promotion gate |
|---|---|---|
| Sharpe (joined) | 1.00 | n/a |
| Profit factor | 1.43 | pass (≥1.2) |
| Max DD | 21.3% | **fail** (≤20%, by 1.3 pp) |
| n_trades | 378 | pass (≥30) |
| Win rate | 46.6% | n/a |
| Per-window Sharpe std/mean | 1.81 | **fail** (≤0.5) |
| Total return | 135% / 8y | ~11.5%/yr |

**Status: marginal — promotable after DD reduction.** Headline metrics are close. Plausible improvements (DO NOT implement without OOS validation): correlation-aware sizing under multi-engine should reduce simultaneous drawdowns; macro_regime_filter (Phase 2d) should suppress entries when 200 SMA / yield curve flips bearish. Re-evaluate after Phase 1c (multi_engine + correlation) lands.

### Joint observations

- Per-window Sharpe std/mean of ~1.7-1.8 in both strategies signals strong regime dependence — not unique to either rule. Multi-strategy diversification (Phase 1c) should compress this.
- Total returns are real but Sharpe is constrained by drawdown. Filter improvements (gap_levels, news_filter, macro_regime) hit drawdown more than they hit Sharpe directly.
- Both strategies pass `n_trades` minimum, so the universe loader's expansion to 9-14 tickers is sufficient. The single-ticker baseline (`mr_etf` at 6 trades) really was statistical noise.

## Rejection rules

- Reject any strategy that needs true HFT infrastructure.
- Reject any "95% win-rate" source until our shadow simulator proves copyable fills after fees.
- Reject politician-copy trades as direct signals due to disclosure delay.
- Reject pyramiding until the base strategy is already profitable in live paper.
- Reject paid-course claims unless the rules can be stated in code and backtested with no discretion.
