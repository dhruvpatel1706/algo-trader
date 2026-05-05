# Strategy Catalog (Replicable Research)

**Purpose**: a curated list of academic + competition-vetted trading strategies that this repo could plausibly implement. The catalog is honest about what is known versus guessed: every entry has a "why it might not work for us" section, every Sharpe number cites the paper or is marked `(not reported)` / `(operator must reproduce)`.

**This is research, not a trading permission slip**. Numbers in published papers are pre-cost, often in-sample, and frequently fail out-of-sample. See `docs/cautionary_tales.md` for what happens when traders forget this. See `docs/research_sources.md` for where to scout for new candidates beyond what is cataloged here.

The schema for each entry:

- **Source / Citation / Asset class / Holding period / Reported Sharpe / Replication difficulty**
- **Logic** — the rule
- **Why it works (claimed)** — the rationale per the source
- **Why it might not work for us** — caveats
- **Implementation in this repo** — what we'd build
- **Validation history** — known failures and retractions

---

## 1. Cross-sectional 12-1 momentum

**Source**: Academic (foundational paper + cross-asset extension)
**Citation**: Jegadeesh, N. & Titman, S. (1993). "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency." *Journal of Finance*, 48(1), 65–91. Asness, C., Moskowitz, T., Pedersen, L.H. (2013). "Value and Momentum Everywhere." *Journal of Finance*, 68(3), 929–985.
**Asset class**: equity (single names, also extended to country indices, currencies, bonds, commodities)
**Holding period**: monthly rebalance, ~21d holding (skipping the most recent month)
**Reported Sharpe**: Asness et al. (2013) report long-short momentum portfolios with Sharpe in the ~0.5–0.8 range on US equities, ~1.0+ across the global multi-asset combination after risk parity weighting. Original Jegadeesh-Titman did not report Sharpe directly; reported abnormal returns of ~1% per month before costs.
**Replication difficulty**: moderate — needs survivorship-bias-free constituent history and point-in-time membership.

### Logic
Each month, rank a universe (e.g., S&P 500 constituents) by total return over months t-12 through t-2 (skip the most recent month to avoid the short-term reversal effect). Go long the top decile, short the bottom decile, equal-weighted. Hold one month, rebalance.

### Why it works (claimed)
Behavioral underreaction to news plus a slow-diffusion-of-information story. Asness/Moskowitz/Pedersen also document momentum across asset classes, suggesting a broader risk premium rather than a single-market anomaly.

### Why it might not work for us
- Severe drawdowns during momentum crashes (March-May 2009 was textbook: long-momentum portfolios lost ~70% peak-to-trough as the post-Lehman bounce killed shorts in beaten-down names — see Daniel & Moskowitz 2016 "Momentum Crashes").
- Capacity-constrained at large AUM (top-decile names have predictable flow).
- Short leg requires borrow availability and is expensive on small-caps.
- Long-only variant has weaker Sharpe but is a more honest test for retail.
- Survivorship bias in any historical S&P 500 list inflates results dramatically.

### Implementation in this repo
Already partial: `src/strategies/momentum_xs.py` exists. Universe key: `large_caps_50`. Indicators needed: only `pct_change(lookback=252).shift(21)`-style logic on `close`. Long-only variant fits the existing `Engine`. A long-short version would need short-side support (currently absent in the backtest engine; see backtest README).

### Validation history (LIES we're guarding against)
- Many published "enhanced momentum" overlays (volatility scaling, idiosyncratic momentum, etc.) have failed out-of-sample in the 2018–2022 period. Treat overlays with extreme skepticism.
- McLean & Pontiff (2016, "Does Academic Research Destroy Stock Return Predictability?") found ~50% post-publication decay in published anomalies; momentum decayed less than most but did decay.

---

## 2. Time-series momentum

**Source**: Academic
**Citation**: Moskowitz, T., Ooi, Y.H., Pedersen, L.H. (2012). "Time Series Momentum." *Journal of Financial Economics*, 104(2), 228–250.
**Asset class**: cross-asset (equity index futures, bond futures, currencies, commodities — 58 instruments in the original)
**Holding period**: monthly, with 12-month lookback
**Reported Sharpe**: ~1.4 for the diversified, vol-scaled portfolio across 58 futures instruments per Table 2 of the original paper (1985–2009). Single-instrument Sharpes are much lower (~0.2–0.5).
**Replication difficulty**: moderate — needs futures price history and roll-adjusted continuous contracts; also needs vol scaling.

### Logic
For each instrument, compute the past 12-month excess return. If positive, go long; if negative, go short. Position size scales by 1/realized-volatility so each instrument contributes equal risk. Rebalance monthly.

### Why it works (claimed)
Same behavioral underreaction story as cross-sectional momentum, but applied per-instrument rather than relatively. Also captures slow-moving macro regimes (interest rate trends, FX trends) that single-stock momentum misses.

### Why it might not work for us
- Sideways markets (2011–2013, 2015–2016 in some asset classes) destroy trend-followers — multi-year flat or losing periods are normal.
- Vol scaling is necessary, not optional; without it, low-vol instruments dominate.
- The headline Sharpe comes from diversification across uncorrelated trends. With only equity ETFs, the Sharpe collapses.
- Retail can't trade 58 futures with sane round-lot sizing on a small account.

### Implementation in this repo
Approximation possible with ETF proxies: `SPY/QQQ/IWM` (equities), `TLT/IEF` (bonds), `GLD/SLV` (commodities), `UUP` (USD), `EEM/EFA` (international). Rule: if 12m return > 0, allocate to that ETF, sized inversely to 12m realized vol. This is the spirit of "AQR Managed Futures Strategy Fund" with retail-grade instruments. Indicators: `pct_change` and `rolling_std`. Fits `Engine` with a thin universe-loop wrapper.

### Validation history
- Live AQR / Man AHL / Winton trend funds underperformed the academic Sharpe by roughly half through the 2010s. Some recovered in 2022. The decay is real and ongoing.
- Hurst, Ooi & Pedersen 2017 "A Century of Evidence on Trend-Following Investing" (entry #9 below) is a defensive response to those concerns — read both.

---

## 3. Connors RSI(2) mean reversion

**Source**: Practitioner / book
**Citation**: Connors, L. & Alvarez, C. (2009). *Short Term Trading Strategies That Work*. TradingMarkets Publishing. (Larry Connors has written several iterations; the RSI(2) trigger is the canonical version.)
**Asset class**: equity (originally US large-cap stocks and index ETFs; also tested on global ETFs in subsequent Connors books)
**Holding period**: 1–5 days
**Reported Sharpe**: Not reported in academic form. Connors' books quote win-rates of 65–75% on RSI(2)<5 setups in SPY 1995–2008 backtests, but these are not survivorship-bias-controlled and don't model slippage. (operator must reproduce)
**Replication difficulty**: trivial — daily OHLC and RSI(2) is enough.

### Logic
On a liquid index ETF, when RSI(2) < 5 (or < 10 for the gentler version), buy at the close. Exit when price closes above the 5-day SMA, or after N days. Optional: only take signals when price is above the 200 SMA (long-term trend filter).

### Why it works (claimed)
Short-term mean reversion in liquid index ETFs is a genuine documented effect: oversold short-window RSI tends to bounce within a few days. The broader 200 SMA filter rejects the "catching falling knives in a bear" failure mode.

### Why it might not work for us
- The book's 65–75% win rate cherry-picks a regime (1995–2008 included some of the strongest mean-reverting tape in modern history).
- Post-2010, the same rule's win rate compresses; profit factor often hovers near 1.0 after costs.
- During genuine corrections (Q4 2018, March 2020, 2022 bear market) "oversold" stays oversold and stops compound.
- Without a stop-loss the strategy has unbounded left tail per trade. Adding a stop drops the win rate.

### Implementation in this repo
**Already partially implemented**: `src/strategies/mr_etf.py` is a Connors-flavored mean-reversion (Bollinger lower band + RSI(2)<10 + ADX gate). The ADX gate is our addition; classic Connors does not require it. Universe: `spy_qqq`. No new indicators needed.

### Validation history
- Many published Connors-style backtests fail to model dividends, splits, and bid-ask realistically. Our `data/` loader uses `auto_adjust=True` per `5fbc192` commit history, so corporate-action bias is reduced but not zero.
- Most retail "RSI(2) edge" claims rely on un-stopped trades. Don't.

---

## 4. Bollinger band reversion with ADX gate

**Source**: Practitioner consensus (Bollinger 2001, Wilder 1978 for ADX)
**Citation**: Bollinger, J. (2001). *Bollinger on Bollinger Bands*. McGraw-Hill. Wilder, J.W. (1978). *New Concepts in Technical Trading Systems* — original ADX paper.
**Asset class**: equity (any liquid instrument, but documented best on index ETFs)
**Holding period**: 1–10 days
**Reported Sharpe**: (not reported in primary sources) — ad hoc backtests in trading-blog territory cite Sharpe 0.3–0.8 on SPY.
**Replication difficulty**: trivial.

### Logic
When price closes at or below the lower Bollinger band (20 SMA, 2 std) AND ADX(14) < 20 (low-trend regime), enter long at the close. Exit on a touch of the middle band (20 SMA) or a fixed-day timeout.

### Why it works (claimed)
Bollinger bands compress in low-vol regimes, making "outside the band" a meaningful 2-sigma deviation. ADX < 20 confirms a non-trending tape, which is a necessary condition for mean reversion. The combination filters the "catching falling knives" trend-mode failure.

### Why it might not work for us
- Bollinger bands are not stationary: in a vol-expansion regime, a "2 std" band excursion is much smaller in absolute terms than in a vol-compression regime.
- ADX is a lagging indicator. A genuinely trending market may still flash ADX<20 briefly before the trend resumes.
- Many "Bollinger reversion wins" are explained by simple buy-the-dip on uptrend; the band itself adds little once you control for the 200 SMA filter.

### Implementation in this repo
**Already implemented as `mr_etf.py`**: Bollinger(20, 2) + RSI(2)<10 + ADX(14)<20 is essentially this strategy with an extra RSI filter. Indicators in `src/signals/indicators.py`: `bollinger_bands`, `rsi`, `adx`, `atr` — all present.

### Validation history
- Bollinger himself warns that the bands are diagnostic, not signals. Practitioners who treat them as standalone triggers get the worst of mean reversion without the higher-prior trend filter.
- A 2018 SSRN paper by Lubnau & Todorova ("Trading on Mean-Reversion in Energy Futures Markets") found that Bollinger-only rules failed in commodity futures once costs were included. Mean reversion is regime-dependent.

---

## 5. Quality minus Junk (QMJ)

**Source**: Academic
**Citation**: Asness, C., Frazzini, A., Pedersen, L.H. (2019). "Quality Minus Junk." *Review of Accounting Studies*, 24(1), 34–112.
**Asset class**: equity (single-stock long-short, US and 23 other markets)
**Holding period**: monthly rebalance
**Reported Sharpe**: ~0.7 for the US QMJ portfolio (1957–2016); ~0.9 globally aggregated. Reported in Table 4 of the published paper.
**Replication difficulty**: hard — needs fundamental data (Compustat-equivalent) and a multi-factor scoring composite.

### Logic
For each stock, compute a composite "quality" score combining profitability (gross profits / assets, ROE), growth (5-year change in profitability), safety (low beta, low leverage, low earnings volatility), and payout (high net payout / equity). Long the top quintile of quality, short the bottom (junk).

### Why it works (claimed)
Quality firms are persistently underpriced relative to expected fundamentals — a "quality risk premium" hypothesis. The paper argues quality is not subsumed by Fama-French factors.

### Why it might not work for us
- Requires fundamental data — Polygon/yfinance free tiers do not provide reliable point-in-time fundamentals. Survivorship bias is pernicious in fundamental data.
- The composite score has 8+ knobs; in-sample tuning is easy, out-of-sample is brutal.
- QMJ underperformed materially in 2020–2021 when "junk" rallied with the meme regime.
- Retail can't short illiquid junk-side names cheaply.

### Implementation in this repo
**Blocked on fundamental data infra**. Would need: a fundamentals provider (Sharadar/Compustat equivalent — not free). Universe: `large_caps_50` could be a starting point, but real QMJ needs a 1000+ name universe. No indicators in `src/signals/indicators.py` apply directly; this is a fundamental-factor strategy, not a price-action one.

### Validation history
- Asness defended QMJ in a 2020 AQR commentary after a 2019–2020 underperformance; the factor recovered through 2022.
- Many retail "quality factor" portfolios are ill-defined and overfit to backtest. Use the AQR factor library construction as the only honest benchmark.

---

## 6. Betting Against Beta (BAB)

**Source**: Academic
**Citation**: Frazzini, A. & Pedersen, L.H. (2014). "Betting Against Beta." *Journal of Financial Economics*, 111(1), 1–25.
**Asset class**: equity (single-stock long-short; also extended to bonds, futures)
**Holding period**: monthly rebalance
**Reported Sharpe**: ~0.78 for the US equity BAB factor (1926–2012) per Table 2 of the original paper.
**Replication difficulty**: moderate — needs full-history beta estimation and leverage on the long side.

### Logic
Estimate each stock's beta to the market (5-year rolling). Construct a beta-neutral long-short: long low-beta stocks levered up to beta=1, short high-beta stocks delevered to beta=1. Rebalance monthly.

### Why it works (claimed)
Leverage-constrained investors (mutual funds, pensions) bid up high-beta stocks to gain return-without-leverage; the resulting overpricing produces a low-beta premium. This is a leverage-constraint hypothesis.

### Why it might not work for us
- The strategy requires actual leverage on the long side. Retail margin is expensive relative to the assumptions in the paper.
- The reported Sharpe includes a multi-decade window where leverage was cheap; recent costs are higher.
- BAB underperformed in 2018–2020 when high-beta tech outran low-beta defensives.
- Replicators have noted that BAB's reported risk-adjusted returns are sensitive to the rank-weighting scheme; an alternative weighting weakens the result substantially (see Novy-Marx & Velikov 2022 critique).

### Implementation in this repo
**Blocked on**: short-side support in the backtest engine + retail-leverage modeling. Would need a beta estimator (rolling regression of asset returns on SPY returns) — which is a one-line addition to `src/signals/indicators.py`. Universe: `large_caps_50`. Long-only variant ("low beta tilt") is feasible without short side, but it's no longer BAB; it's just defensive.

### Validation history
- Novy-Marx & Velikov (2022, "Betting Against Betting Against Beta") replicated the result with alternative weighting and found a significantly weaker effect, arguing the original construction is non-standard.
- BAB's live performance via the AQR Style Premia Alternative Fund underperformed expectations through the 2010s.

---

## 7. Volatility risk premium / VIX term structure

**Source**: Practitioner + academic
**Citation**: Carr, P. & Wu, L. (2009). "Variance Risk Premiums." *Review of Financial Studies*, 22(3), 1311–1341. Practitioner: numerous CBOE white papers; the "short VXX" trade was widely documented in trading blogs from 2011–2018.
**Asset class**: equity volatility (VIX futures, VXX, SVXY, XIV historically)
**Holding period**: continuous (rolling short-vol exposure) or event-driven (post-spike)
**Reported Sharpe**: Carr & Wu document a substantial implied-vs-realized variance spread but do not publish a tradeable Sharpe directly. Practitioner XIV/SVXY backtests pre-2018 cited Sharpe 1.0–1.5, but those were destroyed in February 2018 (see `docs/cautionary_tales.md` entry on VIX-mageddon).
**Replication difficulty**: moderate to hard.

### Logic
Implied vol systematically exceeds realized vol (the "variance risk premium") because option buyers pay a premium for crash insurance. Sell that insurance via short VIX futures, short VXX (long-vol ETN), or long SVXY (inverse-vol ETF) when the VIX term structure is in contango (VIX9D < VIX < VIX3M < VIX6M).

### Why it works (claimed)
Investors are net buyers of crash protection; sellers earn the premium for bearing tail risk. The contango of the VIX futures curve is a near-direct cost-of-carry transfer.

### Why it might not work for us
- **Catastrophic tail risk.** XIV (the inverse-VIX ETN) was terminated on Feb 5, 2018 after a single-day loss of ~96%. SVXY survived only because Credit Suisse rebalanced its leverage cap. Anyone running this strategy with leverage at the wrong moment was wiped out — see entry "VIX-mageddon" in `docs/cautionary_tales.md`.
- The strategy's negative skew is severe: many small wins, occasional 100% losses.
- Modeling the VIX futures roll yield correctly is non-trivial; many retail backtests assume frictionless roll and overstate returns.
- The trade depends on persistent contango; backwardation regimes (Q1 2020, Q1 2022) destroy short-vol PnL.

### Implementation in this repo
**Blocked on**: VIX futures and VIX term-structure data (Polygon/yfinance: limited; CBOE: paid). VXX/SVXY ETF prices are available, but a clean backtest needs the futures curve, not just ETF NAVs. **Strongly recommended**: do not implement until risk caps include explicit VaR-style tail constraints. The strategy is incompatible with the current `MAX_PORTFOLIO_HEAT = 6%` because daily risk is non-Gaussian in a way 6% does not begin to capture.

### Validation history
- **Feb 5, 2018** is the canonical "Volpocalypse" / VIX-mageddon event: XIV terminated, SVXY barely survived. The claimed Sharpe of pre-2018 short-vol strategies was a survivorship illusion.
- Multiple "vol selling" hedge funds (Catalyst Hedged Futures, OptionSellers.com 2018) blew up using related variants. Naked premium selling on tail-risk underlyings is an empirically demonstrated path to ruin.

---

## 8. Triple-barrier labeling for ML overlay

**Source**: Academic / book
**Citation**: López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. Specifically Chapters 3 (labeling), 4 (sample weighting), 7 (cross-validation). The companion book by López de Prado (2020) *Machine Learning for Asset Managers* extends some of these.
**Asset class**: any (this is a meta-labeling technique, not a strategy)
**Holding period**: depends on the underlying primary model
**Reported Sharpe**: López de Prado does not publish a strategy-level Sharpe; the technique is presented as a labeling improvement, not a standalone alpha.
**Replication difficulty**: moderate (the math is straightforward; the discipline of avoiding lookahead leak is the hard part).

### Logic
Given a primary model that emits long/short signals, label each signal's outcome by which of three barriers is hit first: (a) profit-target barrier (k * ATR above entry), (b) stop barrier (k * ATR below), or (c) time-decay barrier (N bars). The label is +1 / -1 / 0. Train a secondary "meta-model" (gradient boosting, etc.) to predict which signals will hit the profit barrier; size up or filter accordingly.

### Why it works (claimed)
Most ML-on-finance failures stem from sloppy labeling (predict the next-day return is a noisy, near-zero-mean target). Triple-barrier labels are economically meaningful (did the trade work?), are properly aligned with realized risk via the volatility-scaled barriers, and reduce noise dramatically. The meta-model then learns when to trust the primary signal.

### Why it might not work for us
- Lookahead bias is everywhere in unsophisticated implementations. Ensure barriers are walked forward in real bar-time, not vectorized over future data.
- The primary model must already have a positive base-rate edge. Meta-labeling a coin flip just yields a more confident coin flip.
- Hyperparameter tuning the barriers and the meta-model is a path to severe overfit. López de Prado's chapters on combinatorial purged cross-validation (CPCV) are mandatory reading and rarely followed.
- Most retail "ML for trading" projects on GitHub use random train/test splits on time-series data — this is the canonical mistake.

### Implementation in this repo
**Could be implemented as a post-processing layer on `src/strategies/*` outputs**. Each strategy already emits `Signal` objects with entry/stop/target — that's a triple-barrier specification by construction. A meta-labeling layer would: (1) replay historical signals, (2) compute the realized barrier outcome, (3) train a classifier (sklearn/lightgbm) on signal features (RSI, ADX, ATR percentile, time of day) to predict outcome. Output: a confidence multiplier on `Signal.confidence`. Universe: any. Indicators: existing.

### Validation history
- Many "AFML"-inspired GitHub repos cherry-pick chapter implementations and skip the CPCV chapter. The result: spectacular in-sample backtests, zero out-of-sample edge.
- López de Prado has been clear that the book is a methodology, not a strategy. Treat it that way.

---

## 9. CTA trend following

**Source**: Academic / industry
**Citation**: Hurst, B., Ooi, Y.H., Pedersen, L.H. (2017). "A Century of Evidence on Trend-Following Investing." *Journal of Portfolio Management*, 44(1), 15–29.
**Asset class**: cross-asset futures (equities, bonds, currencies, commodities)
**Holding period**: weeks to months (trend persistence horizon)
**Reported Sharpe**: ~1.0 for the diversified, vol-scaled multi-instrument trend portfolio over 110 years (1880–2013) per the paper. Recent decades: lower. The CS Tremont Managed Futures index Sharpe over 1994–2017 is closer to 0.4.
**Replication difficulty**: moderate.

### Logic
For each instrument, take a position in the direction of the recent trend (signs of the 1-, 3-, and 12-month excess returns; majority vote or weighted average). Size each position inversely to its realized vol. Diversify across many instruments.

### Why it works (claimed)
Trends arise from initial underreaction to news, followed by herding, followed by overreaction — a behavioral cycle documented across asset classes and centuries.

### Why it might not work for us
- The "century" Sharpe is back-calculated using imputed returns for instruments that didn't exist in their current form (e.g., S&P 500 futures pre-1982). Take it as suggestive, not as a real trading record.
- Recent decade (2011–2020) trend following Sharpe was meaningfully below the long-run number. AQR's own Managed Futures Strategy Fund (AQMIX) underperformed expectations through that window; 2022 partly redeemed it.
- Implementation requires actual futures, not ETF proxies, for clean roll-yield modeling.
- Trend strategies have multi-year flat or losing periods; institutional patience is required.

### Implementation in this repo
**Already partially implemented**: `src/strategies/ma_pullback_trend.py` is a single-instrument trend pullback (20/200 SMA + ATR pullback gate). It is closer to a single-instrument trend-pullback than a multi-instrument vol-scaled CTA. The full version would need: (1) multiple uncorrelated ETFs, (2) per-instrument vol scaling, (3) signal aggregation across timeframes (1m/3m/12m). Universe: a multi-asset version of `liquid_etfs_top20`. Indicators: `sma`, `atr` (existing), plus `rolling_std` for vol scaling.

### Validation history
- Hurst et al. is methodologically sound but uses imputed pre-1980 data; do not over-weight the long sample.
- Live CTA index returns have shown documented decay from the academic numbers — Mulvey (2016) and others have reported the gap.
- Don't confuse "trend following" with "buy and hold": trend-following loses in long sideways markets, where buy-and-hold is at least flat.

---

## 10. PEAD — Post Earnings Announcement Drift

**Source**: Academic
**Citation**: Bernard, V.L. & Thomas, J.K. (1989). "Post-Earnings-Announcement Drift: Delayed Price Response or Risk Premium?" *Journal of Accounting Research*, 27, 1–36. Surveyed and updated by Chordia & Shivakumar (2006) and others.
**Asset class**: equity (single names)
**Holding period**: ~60 trading days post-announcement
**Reported Sharpe**: Bernard & Thomas (1989) report quarterly returns of ~5% for the long-short top-vs-bottom standardized-unexpected-earnings (SUE) decile portfolio over 60 days. Subsequent papers (Sadka 2006) confirm the effect persists but is reduced after costs; tradeable Sharpe estimates are in the 0.5–1.0 range for sophisticated implementations. (operator must reproduce)
**Replication difficulty**: hard — needs analyst earnings consensus + actuals data.

### Logic
After a company reports earnings, compute the standardized unexpected earnings (SUE = (actual - consensus) / std(consensus error)). Stocks with positive SUE drift up over the next ~60 days; stocks with negative SUE drift down. Long top-decile SUE, short bottom-decile, hold 60 days, rebalance.

### Why it works (claimed)
Behavioral underreaction to earnings news; analysts and investors update slowly. Possibly also an attention-allocation friction.

### Why it might not work for us
- Requires earnings data with point-in-time consensus estimates (I/B/E/S, FactSet, or Estimize) — not free.
- The drift has decayed substantially post-2003 (post-decimalization, post-Reg FD). Recent estimates are ~half the 1980s magnitude.
- Earnings-day overnight gaps blow up the strategy if entries are at the wrong side of the gap.
- High turnover; transaction costs eat the edge for retail.

### Implementation in this repo
**Blocked on**: earnings/consensus data feed. Polygon offers earnings dates but not consensus estimates on the free tier. yfinance has spotty consensus data. Universe: `large_caps_50` is too small for proper decile portfolios; would need 500+ names.

### Validation history
- Chordia, Goyal, Sadka, Sadka, Shivakumar (2009) found PEAD persists primarily in small-caps and decays out of large-caps — the regime where retail can actually trade is also the regime where the effect is weakest after costs.
- Several "PEAD ETF" launches (e.g., 2018-vintage smart-beta funds) have failed to deliver the academic Sharpe.

---

## 11. Funding rate arb on perpetual futures (crypto)

**Source**: Practitioner (well-documented in crypto blogs and exchange research, 2017–2021 era)
**Citation**: BitMEX Research blog (archived at https://blog.bitmex.com — note: BitMEX Research itself wound down in 2022; many posts only exist via Wayback Machine). Also: Binance Academy and FTX Research (defunct).
**Asset class**: crypto (perpetual futures vs spot)
**Holding period**: continuous (positions held as long as funding stays favorable)
**Reported Sharpe**: (not reported in academic form). Practitioner blogs from 2018–2021 cited annualized returns of 15–40% on capital deployed, but these did not properly account for exchange counterparty risk, liquidation risk in margin pairs, or stablecoin risk.
**Replication difficulty**: moderate operationally, hard from a counterparty-risk perspective.

### Logic
A crypto perpetual future has no expiry; it tracks spot via a periodic "funding rate" paid between longs and shorts (typically every 8 hours). When funding is positive, longs pay shorts. When the perpetual trades at a premium to spot AND funding is positive AND the funding rate exceeds the borrowing/transaction cost, you can short the perp and go long spot in equal size, pocketing the funding rate with negligible directional exposure.

### Why it works (claimed)
Retail traders on crypto exchanges pay persistently to be long; the funding mechanism transfers their premium to the short side. This is a microstructure / sentiment premium that has been remarkably persistent across cycles.

### Why it might not work for us
- **Counterparty risk is not bounded.** Many of the exchanges where this trade was profitable have failed: FTX (2022), Mt. Gox (2014), QuadrigaCX (2019), Celsius (2022). A 30%/yr funding-arb return is unattractive when the off-ramp is "hope the exchange is still solvent on Monday."
- Stablecoin de-pegging risk: USDT and USDC have both traded below par. If you collateralize with one, your "delta-neutral" trade has tail-correlated risk to the stablecoin issuer.
- Liquidation risk: even a delta-neutral pair can be liquidated if margin requirements move asymmetrically during a flash crash.
- Funding rates can flip negative in extended bear markets (long-bias compresses), inverting the trade.

### Implementation in this repo
**Out of scope**. Blocked on: a vetted crypto exchange integration (and we're not adding one without significant risk-policy work) plus stablecoin counterparty modeling. The thesis is documented for completeness only. Don't trade this without first reading every entry in `docs/cautionary_tales.md`.

### Validation history
- Multiple "delta-neutral crypto yield" funds vaporized in 2022 (Celsius, Voyager, Three Arrows Capital — though 3AC's failure was more about leverage than funding-arb specifically).
- BitMEX Research itself acknowledged in retrospective posts that early funding-arb backtests did not properly model exchange-default risk.

---

## 12. Triple-witching options dealer-gamma flow

**Source**: Academic / industry research
**Citation**: Brogaard, J., Carrion, A., Moyaert, T. (2018-2021 series of papers on options market-maker hedging). Also: SqueezeMetrics white papers and Goldman Sachs derivatives research notes from 2019–2021. Note: this is an active research area without a single canonical citation; the operator should triangulate across multiple sources.
**Asset class**: equity (S&P 500 index options exposure transmitted to underlying)
**Holding period**: hours to ~3 days around options expiration events
**Reported Sharpe**: (not reported in academic form). Industry practitioners (SpotGamma, SqueezeMetrics) publish indicative dealer-gamma snapshots but not a tradeable Sharpe. (operator must reproduce)
**Replication difficulty**: hard — needs full options chain with bid/ask, open interest, and a dealer-positioning model.

### Logic
Options market makers hedge their delta exposure dynamically. When dealers are long gamma (long calls / short puts net), they supply liquidity, and SPY tends to be range-bound. When dealers are short gamma (short calls / long puts net), they amplify moves — they buy as price rises, sell as price falls. The "zero gamma flip level" (computed from open-interest distribution) and triple-witching options expirations (3rd Friday of every month + extra quarterly OPEX) are inflection points where dealer hedging flow reverses.

### Why it works (claimed)
Dealer hedging is a mechanical, large, and predictable flow. If you can model it, you can position ahead of the squeeze or fade the over-extension.

### Why it might not work for us
- Requires options chain data with full open interest and put/call breakdown. Polygon has options data on paid tiers; yfinance's options data is unreliable.
- The "zero gamma" calculation is non-trivial and assumes a Black-Scholes constant-vol world that doesn't fit reality. Different vendors compute it differently and disagree by hundreds of S&P points.
- The dealer-positioning thesis is easy to overfit. Many "OPEX trading" rules show gorgeous in-sample results and deteriorate dramatically out-of-sample.
- Even when the directional thesis is right, the entry and exit timing is brittle; many trades that "should have" worked get stopped out by intraday noise.

### Implementation in this repo
**Blocked on**: options chain data + a dealer-gamma model. Existing `src/strategies/wheel_etf.py` is a stubbed-out CSP wheel that itself is blocked on options data; this strategy is an even harder lift. Universe: SPX/SPY only initially. Indicators: none of the existing apply; this needs a full options-engine rebuild. **Strongly recommended**: do not implement before the wheel un-stub lands and Polygon options integration is verified.

### Validation history
- This entry is included for *context*, not as a near-term implementation candidate. The dealer-gamma thesis is likely real at large index level but extracting tradable retail edge is not demonstrated in publicly-replicable form.
- SqueezeMetrics and SpotGamma sell their dealer-gamma data; their public examples are necessarily curated.

---

## Honest mapping to this repo

### What we ALREADY HAVE in some form

- **Connors RSI(2) (entry #3)**: `src/strategies/mr_etf.py` — Bollinger lower band + RSI(2)<10 + ADX gate. The ADX gate is our addition; classical Connors does not require it.
- **Bollinger band reversion with ADX gate (entry #4)**: same file (`mr_etf.py`) — this is essentially the literature-canonical Bollinger reversion with the trend-regime gate that classical Connors leaves out.
- **CTA trend following (entry #9)** — partial: `src/strategies/ma_pullback_trend.py` is a single-instrument trend pullback. Real CTA needs multi-instrument, vol-scaled aggregation.
- **Cross-sectional momentum (entry #1)** — partial: `src/strategies/momentum_xs.py` exists per the strategy-universe map in `docs/universes.yaml`. Long-only; the long-short version is blocked on short-side support in the engine.

### NEXT priority to add (data already available)

1. **Time-series momentum (entry #2)** with ETF proxies (`SPY/QQQ/IWM/TLT/IEF/GLD/EFA/EEM`) — needs only daily OHLC, which we have. Vol-scaling is a one-liner. This is the most-defensible addition with our current data.
2. **Triple-barrier labeling overlay (entry #8)** — works on top of any existing strategy. The `Signal` dataclass already encodes entry/stop/target. The next step is replaying historical signals and meta-modeling the outcome. **Be religious about CPCV**.
3. A diversified version of trend-following (entry #9) — extend `ma_pullback_trend` to a multi-asset, vol-scaled basket. Mostly reuses existing indicators.

### Blocked on infra (do not start before infra lands)

- **Volatility risk premium / VIX (entry #7)**: needs VIX futures curve + explicit tail-risk caps. **Don't do this before risk-policy work**.
- **Quality minus Junk (entry #5)**: needs a fundamentals data feed (Sharadar/Compustat-equivalent).
- **Betting Against Beta (entry #6)**: needs short-side engine support + retail-leverage modeling.
- **PEAD (entry #10)**: needs point-in-time earnings consensus data.
- **Funding-rate arb (entry #11)**: needs vetted crypto exchange integration. Counterparty risk modeling is the blocker, not the math.
- **Dealer-gamma flow (entry #12)** and **wheel_etf**: both blocked on Polygon options data + options-aware engine. Wheel is the prerequisite; dealer-gamma is downstream of that.
