# Cautionary Tales (Trading System Blow-ups)

**Purpose**: counterpart to `docs/strategy_catalog.md`. Where the catalog lists strategies that *might* work, this document catalogs strategies (and operations) that did *not*. Each entry maps explicitly to risk caps in `src/config.py`.

The risk caps in this repo:
- `MAX_PER_TRADE_RISK = 1%`
- `MAX_PORTFOLIO_HEAT = 6%`
- `MAX_SINGLE_POSITION = 10%`
- `DAILY_LOSS_HALT = -2%`
- `DRAWDOWN_HALT = 15%`
- `LIVE_TRADING = "0"` (paper-only)

These numbers are not arbitrary. Each entry below points to the specific blow-up that informs its existence.

---

## 1. Long-Term Capital Management (1998)

**Year**: 1998 (collapse September 1998; Fed-coordinated bailout)
**What they did**: Convergence and relative-value arbitrage across global fixed income (on-the-run vs off-the-run Treasury spreads; sovereign bond pairs; merger arb; equity vol). Two Nobel laureates (Merton, Scholes) on the team. Reported Sharpe ~4 from 1994–1997.
**How they blew up**: Russia defaulted in August 1998. Liquidity evaporated globally. LTCM's pairs that "should have" converged diverged instead, because everyone holding similar trades was forced to unwind into a falling market simultaneously. With ~25:1 leverage, modest mark-to-market drawdowns translated into catastrophic equity destruction. The fund lost $4.6 billion in four months.
**The math that lied**: VaR models calibrated on the 1990s' relatively stable credit spreads catastrophically underestimated the tail of correlated cross-asset moves. When the unthinkable happened, the model said it was an 8-sigma event — except such events happen far more often than Gaussian models predict.

### Lessons for us (mapped to `src/config.py`)
- **`MAX_PORTFOLIO_HEAT = 6%`, not 60%**: LTCM's lesson is that "uncorrelated" positions become correlated under stress. Even if our individual strategies have low pairwise correlation in normal conditions, a 6% portfolio heat ceiling means a single shock day cannot destroy the account.
- **`DRAWDOWN_HALT = 15%`**: LTCM let drawdowns compound past the point where recovery was mathematically possible at their leverage. A hard halt at 15% is conservative-by-design; it forces re-evaluation before catastrophe.
- **No leverage**: LTCM's edge per unit risk was real; their leverage made it lethal. This repo does not use margin.

---

## 2. Knight Capital (2012)

**Year**: August 1, 2012
**What they did**: Equity market making, retail order flow execution, broker-dealer.
**How they blew up**: A botched software deployment to Knight's automated trading system. New code was deployed to seven of eight servers; one server still ran an old code path. The old path repurposed a flag that the new code used for a different purpose. When the flag was flipped at market open, the stale server began sending parent orders without slicing them. Knight bought $7 billion of equities and sold $3.5 billion in 45 minutes, accumulating a $440 million loss. Knight was acquired by Getco shortly after.
**The math that lied**: The math was fine. The deployment process was not. There was no kill-switch on aggregate position size; the only stop was the human.

### Lessons for us (mapped to `src/config.py`)
- **`LIVE_TRADING = "0"` by default**: Knight didn't have a live-trading guard at the deployment level; their deployment process treated stale-server detection as an operational concern. Our `LIVE_TRADING` env-gate forces an explicit, deliberate flip from paper to live and is in version-controlled config.
- **`DAILY_LOSS_HALT = -2%`**: Knight's incident hit -440M in 45 minutes. A daily loss halt would have triggered far before then. We need ours functional and tested in paper before any live flip — which is the existing policy.
- **Deployment matters**: Knight's lesson isn't a position-size cap; it's that production deployment of trading code without canarying or staged rollouts is the single most expensive engineering mistake possible. Our deploy strategy must include: paper validation, staged rollouts, and the ability to halt a live system in <60 seconds.

---

## 3. Amaranth Advisors (2006)

**Year**: September 2006
**What they did**: Multi-strategy hedge fund, $9.2B AUM at peak. The energy desk, run by Brian Hunter, ran spread trades on natural gas futures (March-vs-April calendar spreads, etc.).
**How they blew up**: Hunter accumulated a massive, concentrated position in natural gas calendar spreads predicated on a winter cold snap and hurricane season disruption. Hurricane Katrina (2005) had been the previous winning trade; he doubled the bet for 2006. Natural gas prices collapsed. Amaranth's monthly loss hit $6 billion — about 65% of fund AUM in a single month — and the fund liquidated.
**The math that lied**: Position concentration. The risk team's models priced Amaranth's gas exposure as a small fraction of "diversified multi-strat" risk, but the gas desk represented disproportionate marginal risk per dollar of allocated capital. When the position moved against them, exit was impossible without moving the market against themselves further.

### Lessons for us (mapped to `src/config.py`)
- **`MAX_SINGLE_POSITION = 10%`**: no single name is allowed to exceed 10% of equity. Amaranth's natural gas concentration was multiples of its formal risk budget by the end. A hard 10% cap is the structural answer.
- **Sector exposure caps in `docs/universes.yaml`** (`sector_map`): Amaranth's risk wasn't just one ticker; it was one *sector* (energy futures, but specifically the natural gas curve). Our multi-engine sector caps are the analog; without them, "diversified across 10 tech names" is one tech bet, not ten.

---

## 4. Archegos Capital Management (2021)

**Year**: March 2021
**What they did**: Family office (formerly Tiger Asia hedge fund). Bill Hwang built ~$30B+ of synthetic long exposure to a concentrated basket (ViacomCBS, Discovery, Tencent Music, Baidu, GSX, etc.) using total-return swaps with multiple prime brokers.
**How they blew up**: Each prime broker (Goldman, Morgan Stanley, Credit Suisse, Nomura, UBS) saw only its slice of the position. Aggregate exposure was invisible. Hwang's swap collateral structure used ~5x leverage. When ViacomCBS announced an unfavorable secondary offering on March 22, 2021, the basket fell rapidly. Margin calls cascaded across prime brokers; Goldman and Morgan Stanley exited fastest, dumping shares; Credit Suisse and Nomura took the largest losses (Credit Suisse: ~$5.5B, contributing materially to its eventual UBS-takeover demise in 2023).
**The math that lied**: Each individual prime broker's risk model was reasonable on its own slice. The aggregate position — invisible to any single counterparty — was wildly overlevered. Counterparty risk models systematically underestimate the "every prime is the last to know" failure mode.

### Lessons for us (mapped to `src/config.py`)
- **No swap leverage**: this repo trades cash equities only on a paper account. Total-return-swap leverage is structurally outside our risk perimeter.
- **`MAX_SINGLE_POSITION = 10%`**: even cash long, Archegos held ~50% of ViacomCBS float. A 10% per-name cap means we cannot accidentally become a forced-seller.
- **Concentration discipline**: Archegos' lesson is that the same idea expressed in 5 names that all rise/fall together is *one* idea, not five. Our `sector_map` enforces structural diversification.

---

## 5. Salomon Brothers Treasury Auction Scandal (1991)

**Year**: 1991 (Salomon's predecessor of LTCM era)
**What they did**: Salomon Brothers' bond desk submitted improper customer-name bids in US Treasury auctions to corner the on-the-run Treasury issue, exceeding the 35% bidder cap.
**How they blew up**: The SEC and Treasury investigated. Salomon faced existential regulatory penalties; only Warren Buffett's intervention as interim chairman (he took the role personally to negotiate with regulators) saved the firm. The episode is included here because it's the cultural ancestor of LTCM (many LTCM partners came from Salomon's arbitrage desk) and because the lessons about regulatory risk are evergreen.
**The math that lied**: The trade was profitable. The math was profitable. The compliance was not. Trading rules exist for systemic reasons; circumvention is a non-bounded liability.

### Lessons for us (mapped to `src/config.py`)
- **`docs/policy.md` as the source of truth**: `LIVE_TRADING="0"` exists not just because we're not ready operationally but because compliance and policy must precede live trading, not follow it. The Salomon lesson is that even inside large, sophisticated firms, the ops culture decides whether the trade is legal.
- This is an operational-risk lesson, not a position-size lesson. Document the rules. Don't trade outside them. Don't trade live until the policy doc is reviewed.

---

## 6. Bernie Madoff (revealed 2008)

**Year**: revealed December 2008 (operating since at least the 1970s)
**What they did**: Madoff Investment Securities ran a Ponzi scheme posing as a "split-strike conversion" options strategy producing extraordinarily smooth ~10–12%/yr returns.
**How they blew up**: 2008 redemption requests exceeded inflows; the scheme collapsed. Madoff is included in this catalog *not* because of the algorithm (there wasn't one — it was outright fraud) but because the operational-risk and red-flag lessons are central.
**The red flags everyone ignored**: (1) impossibly smooth returns (Sharpe ~4 across decades, with virtually no drawdown), (2) self-custody — Madoff was his own broker, custodian, and administrator — eliminating independent verification, (3) refusal to allow due diligence by sophisticated allocators, (4) a tiny accounting firm auditing a multi-billion-dollar firm, (5) options-volume claims that exceeded total CBOE volume on relevant strikes.

### Lessons for us (mapped to `src/config.py`)
- **Smooth returns are a red flag, not a feature**: any backtest that produces a Sharpe >2 with no drawdown is almost certainly leaking lookahead, ignoring costs, or has a survivorship bias. We treat any internal backtest with Sharpe >2 as suspect-by-default and require explicit walk-forward + cost validation per `docs/edge_research_2026.md`.
- **Self-administration is a red flag**: in this repo's context, this maps to "don't trust internal-only backtests as sole evidence." External validation (paper-trading at a real broker, comparing live fills to backtest assumptions) is mandatory before any LIVE_TRADING flip.
- **Operational separation**: Madoff's blow-up is partly a story about lack of separation between trading, custody, and accounting. The repo equivalent: separation between strategy code, risk caps, and execution. The risk caps in `src/config.py` are validated independently of the strategies (see `_per_trade_risk_bounds` etc.); strategies cannot self-grant exemptions.

---

## 7. VIX-mageddon (Volpocalypse, February 2018)

**Year**: February 5, 2018
**What happened**: The VIX index spiked from ~13 to ~37 in a single trading session, while still well below true panic levels. The XIV inverse-VIX ETN (VelocityShares Daily Inverse VIX Short-Term ETN, issued by Credit Suisse) was structured to terminate if its NAV dropped >80% in a day. After-hours VIX-futures buying (driven by short-vol funds rebalancing into a bid-less market) drove XIV's NAV down ~96% in a single after-hours move. Credit Suisse declared an "acceleration event" the next morning. XIV ceased trading; holders received pennies per share. SVXY (the ProShares inverse-VIX ETF, similar product) survived because ProShares pre-emptively halved its leverage from -1x to -0.5x; even so, SVXY lost ~90% of value over the same period.

**The trades that broke**:
- LJM Preservation & Growth: a $750M mutual fund running short vol; lost ~80% in two days.
- Catalyst Hedged Futures (an earlier 2017 blow-up); short-vol/short-options exposure compounded by similar mechanics.
- OptionSellers.com (later in 2018): naked short calls on natural gas; clients lost in excess of their account values, owing additional money to the broker.

### The math that lied
**Negative skew**. Short-vol strategies have a payout profile of "many small wins, occasional total loss." Sharpe is computed on the realized return distribution, but if the underlying distribution has a fat left tail you haven't sampled yet, your Sharpe is a mirage. Pre-2018 short-vol Sharpes of 1.5+ became fictitious in retrospect.

### Lessons for us (mapped to `src/config.py`)
- **`MAX_PORTFOLIO_HEAT = 6%`**: short-vol strategies have non-Gaussian risk that a 6% heat cap does not protect against in absolute terms. The corollary is: this repo should not run uncovered short-vol exposure even within the formal heat cap. A short-vol strategy entry would require explicit tail-risk constraints (max loss simulated under -10 sigma move) before approval.
- **`DRAWDOWN_HALT = 15%`**: a 15% halt would have stopped LJM-style trades before zero. The trade is: halt early, reassess, or re-enter, vs. ride the position to zero.
- **No leverage**: as in LTCM, leverage on a fat-tailed payout is the path to ruin.
- The wheel-strategy entry in our roadmap (`src/strategies/wheel_etf.py`) is currently a stub specifically because we haven't yet modeled tail risk well enough to run it.

---

## 8. Archegos prime brokers (2021) — counterparty perspective

**Year**: March 2021 (same event as entry #4, viewed from the counterparty angle)
**What they did**: Five prime brokers (Goldman Sachs, Morgan Stanley, Credit Suisse, Nomura, UBS) extended total-return-swap financing to Archegos. Each saw only its own slice. Each judged its own slice as adequately collateralized.
**How they blew up**: When Archegos defaulted, the brokers had to liquidate the underlying equity hedges. The first to liquidate (Goldman, Morgan Stanley) recovered most of their exposure. The slowest (Credit Suisse, Nomura) absorbed the bulk of the losses. Credit Suisse lost ~$5.5B; the event materially weakened CS and contributed to its 2023 emergency takeover by UBS.
**The math that lied**: Each prime's "VaR on this client" was reasonable in isolation. Aggregate exposure was invisible. Concentration risk on the *broker* side — too many concurrent prime relationships financing the same idea — was unmodeled.

### Lessons for us (mapped to `src/config.py`)
- **One broker, one venue**: this repo plans to use a single broker (Alpaca) for paper, with explicit policy review before adding others. This avoids the Archegos-mirror failure mode where positions across venues sum to more than risk caps.
- **All exposure must be visible at one place**: the journal in `journal/` and the risk service must see every fill. There is no "off-platform" position permitted in the design.
- **Counterparty solvency matters**: even on paper, this is a discipline lesson. When LIVE_TRADING flips, broker solvency becomes a real risk factor — see also the FTX (2022), Voyager (2022), and Celsius (2022) crypto-side analogs in entry #11 of `docs/strategy_catalog.md`.

---

## Cross-cutting lessons summary

The risk caps in `src/config.py` are not arbitrary numbers; each is the operational answer to one or more historical disasters:

| Cap | Disaster informing it |
| --- | --- |
| `MAX_PER_TRADE_RISK = 1%` | LTCM (leverage), Amaranth (concentration), VIX-mageddon (left-tail risk per trade) |
| `MAX_PORTFOLIO_HEAT = 6%` | LTCM (correlated unwinds), Archegos (aggregate concentration), VIX-mageddon (fat-tail aggregate exposure) |
| `MAX_SINGLE_POSITION = 10%` | Amaranth (natural gas concentration), Archegos (single-name concentration), Madoff (lack of independent oversight on size) |
| `DAILY_LOSS_HALT = -2%` | Knight Capital (no aggregate kill-switch), VIX-mageddon (one-day terminal events) |
| `DRAWDOWN_HALT = 15%` | LTCM (compounding losses past survivability), VIX-mageddon (recovery-impossible regime) |
| `LIVE_TRADING = "0"` default | Knight Capital (deployment risk), Salomon 1991 (compliance precedes trading), Madoff (operational separation) |

The pattern across every entry: **the math wasn't usually the problem. The operations, the leverage, and the concentration were.** This repo's emphasis on small caps, no leverage, paper-first, and policy-first reflects the historical lesson that survival comes from operations, not alpha.
