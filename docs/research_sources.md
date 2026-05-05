# Research Sources (Where to Scout for New Strategies)

**Purpose**: an index of sources for finding new candidate strategies beyond what's in `docs/strategy_catalog.md`. For each: a short description, its strengths and weaknesses, and watchouts.

The signal-to-noise ratio drops sharply as you move from peer-reviewed academic publications to forum chatter. This document tries to honestly rate each.

---

## Academic and peer-reviewed

### arXiv (q-fin.PM, q-fin.TR, q-fin.MF)

**Type**: pre-print server, finance categories (Portfolio Management, Trading, Mathematical Finance)
**URL**: https://arxiv.org/list/q-fin/recent
**Access**: free, no paywall, no registration

**Good for**: bleeding-edge research, especially ML-heavy methods. Volume is manageable; you can skim the daily list in 5 minutes.

**Not good for**: peer review. Most q-fin posts have not been refereed; methodology is uneven. Many "we found a Sharpe of 3 with our novel deep learning architecture" papers do not survive replication.

**Watchout**: Arxiv preprints heavily favor positive results because they're self-published. Always check whether the paper has a follow-up published version in *Journal of Finance*, *RFS*, *JFE*, *JFQA*, or similar. If two years have passed and the paper is still preprint-only, that's a quality signal.

---

### SSRN (Social Science Research Network)

**Type**: pre-print + working paper repository, finance/economics
**URL**: https://www.ssrn.com/index.cfm/en/
**Access**: free read; some papers paywalled

**Good for**: working papers from major academic finance authors (Fama, Asness, Pedersen, Frazzini all post here). Faster than the journal cycle.

**Not good for**: filtering noise. Daily SSRN posts include many short, weak, uncited papers. Use the "top downloads in last 12 months" listing as a filter, or follow specific authors' SSRN pages.

**Watchout**: like arXiv, biased toward positive results. Many SSRN papers also have data-snooping issues — running the same idea on 50 universes and reporting only the one that worked. Always check sample dates: is the data window suspiciously short or backward-looking?

---

### Quantitative Finance (journal)

**Type**: peer-reviewed academic journal
**URL**: https://www.tandfonline.com/journals/rquf20
**Access**: paywalled; library or Sci-Hub

**Good for**: rigorous methodology on derivatives pricing, market microstructure, ML applications.

**Not good for**: actionable strategies you can paste into code. The journal leans theoretical.

**Watchout**: paywall friction. If you're not at a university, follow the journal's email alerts and request author preprints when something looks promising.

---

### Journal of Portfolio Management

**Type**: peer-reviewed practitioner-facing academic journal
**URL**: https://www.pm-research.com/content/iijpormgmt
**Access**: paywalled; library or institutional access

**Good for**: practitioner-quality factor research (lots of AQR, Research Affiliates, Robeco contributions). Hurst-Ooi-Pedersen "Century of Evidence on Trend-Following" is a JPM paper — that's the level.

**Not good for**: cutting-edge ML methods (those go elsewhere).

**Watchout**: many JPM papers are written by people whose firms sell the strategy described. Read with that conflict of interest in mind. Check the disclosure footnotes.

---

## Competition-vetted

### Numerai signals

**Type**: ongoing rolling tournament; submit a model, get paid in crypto if your predictions outperform
**URL**: https://numer.ai/
**Access**: free to participate

**Good for**: live, ongoing, real-money out-of-sample validation of equity-prediction models. Top-of-leaderboard models occasionally get blogged about by the participant. The infrastructure is genuinely well-designed.

**Not good for**: extracting strategies. The features Numerai provides are obfuscated; you can win without ever knowing what you're predicting on. The leaderboard rewards consistency more than absolute returns.

**Watchout**: Numerai has had multiple format changes; older leaderboard winners are not directly comparable to current ones. The actual "edge" extracted by Numerai's meta-model is opaque even to participants.

---

### Quantiacs futures challenge

**Type**: futures trading competition
**URL**: https://www.quantiacs.com/
**Access**: free to participate; older winners' code archived

**Good for**: futures-specific strategy ideas. Winners' code is published, so you can read the actual rules.

**Not good for**: high-frequency or microstructure strategies (the platform is daily-bar). The competition has had quieter periods; activity has fluctuated.

**Watchout**: top-leaderboard models are often heavily optimized to the specific futures basket and date range Quantiacs uses. Sharpe out-of-sample on different futures or different periods is usually much lower.

---

### Quantopian (archived)

**Type**: historical platform; archive of strategies and tutorials. Quantopian shut down in October 2020, but materials are still findable.
**URL**: GitHub mirrors of Quantopian content; some original notebooks survive at https://github.com/quantopian/research_public
**Access**: free, but discovery requires effort

**Good for**: gold-standard backtest infrastructure (Quantopian's `zipline` is open source and remains the reference for honest, point-in-time backtesting on US equities) and a library of competition-winning strategies whose code is published.

**Not good for**: live signals. The platform is dead; nobody is updating these.

**Watchout**: many Quantopian "winners" had Sharpe >2 in backtest and would not have survived live trading. The platform's backtest tooling was honest, but selection bias on the leaderboard is severe. The lesson from Quantopian is that even with point-in-time data and proper survivorship handling, top-of-leaderboard models often did not paper-trade well — a critical reality check.

---

## Practitioner blogs and curated GitHub

### Hudson & Thames blog

**Type**: practitioner blog adjacent to López de Prado's "Advances in Financial Machine Learning"
**URL**: https://hudsonthames.org/blog/
**Access**: free; some tools require their `mlfinlab` library

**Good for**: implementations of AFML techniques (triple-barrier labeling, fractional differentiation, etc.). High signal because the blog posts mostly track real published methodology.

**Not good for**: novel strategy ideas. The blog is implementation-focused, not idea-generation-focused.

**Watchout**: `mlfinlab` has had a contentious licensing history; verify the current state before depending on it. Some early posts contain implementation bugs that were corrected later.

---

### Robot Wealth blog

**Type**: practitioner blog by Kris Longmore (ex-RW; now a quant investment firm)
**URL**: https://robotwealth.com/
**Access**: free blog; paid courses and Bootcamp

**Good for**: practical, R-and-Python-grounded strategy walkthroughs with explicit cost modeling. Strong honesty about negative results.

**Not good for**: completely novel research; the blog focuses on educational replications of known ideas.

**Watchout**: it is a commercial blog selling courses; certain posts are pitched as teasers. Read for the methodology, not the headline number.

---

### `stefan-jansen/machine-learning-for-trading` (GitHub)

**Type**: companion code to Stefan Jansen's *Machine Learning for Algorithmic Trading* book
**URL**: https://github.com/stefan-jansen/machine-learning-for-trading
**Access**: free, open source

**Good for**: end-to-end implementations of factor-research workflows (feature engineering, point-in-time fundamentals, ML pipelines) on US equities.

**Not good for**: ready-to-trade strategies. The repo is a teaching tool. Many notebooks are deliberately illustrative rather than production-ready.

**Watchout**: data-loading scripts use various commercial sources (Quandl, Sharadar). Some examples will not run without paid data subscriptions.

---

### `je-suis-tm/quant-trading` (GitHub)

**Type**: curated personal collection of trading-strategy implementations in Python
**URL**: https://github.com/je-suis-tm/quant-trading
**Access**: free, open source

**Good for**: a wide breadth of strategies (pairs trading, smart money index, MACD oscillator, Heikin-Ashi, RSI pattern recognition, etc.) with reproducible Jupyter notebooks. Good as an idea catalog.

**Not good for**: rigorous backtesting. Most notebooks are demonstrative — they show *what* a strategy does, not *whether it has positive expected value after costs*. Do not lift Sharpe numbers from this repo.

**Watchout**: the repo is one person's exploration; some implementations have subtle indicator bugs (e.g., index alignment, look-ahead in shifts). Re-implement, don't copy.

---

## Forums (low signal but occasional gems)

### WilmottForums

**Type**: quant-finance forum (mostly archived; activity has dwindled since ~2015)
**URL**: https://forum.wilmott.com/
**Access**: free read; account needed to post

**Good for**: deep historical threads on quant interview questions, derivatives pricing edge cases, and old practitioner debates. The archived "Numerical Methods" and "Brainteaser" sections are still useful.

**Not good for**: current strategy ideas. Most threads are 5–15 years old.

**Watchout**: many threads pre-date modern data and modern markets. Treat as historical reference, not current playbook.

---

### Reddit r/algotrading

**Type**: subreddit; user-generated trading-strategy discussion
**URL**: https://www.reddit.com/r/algotrading/
**Access**: free

**Good for**: occasional well-thought-out posts where someone shares working code with realistic backtests. Community vetting helps; obvious nonsense gets called out.

**Not good for**: anything you take at face value. The signal-to-noise ratio is low; perhaps 1 in 100 posts is substantive. Most "I made a strategy with Sharpe 5" posts are overfit, lookahead-leaking, or outright fabricated.

**Watchout**: never copy code from r/algotrading without understanding every line. Survivorship bias on the front page is severe — only "winning" posts get upvoted, even when the wins are illusory.

---

## Specialized / historical archives

### BitMEX Research archive (largely defunct)

**Type**: crypto-focused research blog from BitMEX exchange (now mostly archived)
**URL**: https://blog.bitmex.com (current); https://web.archive.org/web/*/blog.bitmex.com (Wayback Machine for old posts)
**Access**: free read

**Good for**: deep dives on crypto market microstructure circa 2017–2021 (perpetual futures funding rates, exchange margin mechanics, stablecoin issuance dynamics). The funding-rate-arb writeups remain useful baseline reading.

**Not good for**: current crypto market structure (the exchange landscape has changed dramatically post-FTX). Many of the exchanges discussed have failed.

**Watchout**: read the date stamp. A 2018 post on FTX margin mechanics is now historical curiosity, not actionable. Cross-reference current exchange terms before assuming any 2017–2021 post still applies.

---

### CFA Institute Financial Analysts Journal

**Type**: peer-reviewed practitioner journal
**URL**: https://www.cfainstitute.org/en/research/financial-analysts-journal
**Access**: paywalled; CFA Institute member access

**Good for**: factor-investing research, ESG, asset allocation. Solid peer review.

**Not good for**: high-frequency or alpha-strategy research. The audience is asset allocators.

**Watchout**: industry-funded research; check who paid for the study.

---

## Choosing what to read

Rough heuristic for time allocation:

1. **First**: peer-reviewed academic (JF, JFE, RFS, JPM) — slow but reliable. ~1 paper/week deep read.
2. **Second**: SSRN top-downloads of the past quarter — moderate signal, faster cadence.
3. **Third**: Hudson & Thames or Robot Wealth for implementation-grade walkthroughs — good for methodology.
4. **Fourth**: GitHub repos (`stefan-jansen/machine-learning-for-trading`) for code patterns.
5. **Last**: Reddit and forums — only when something specific brings you there. Never use as a primary discovery mechanism.

The single most common failure mode in retail strategy research is **chasing the source's headline Sharpe number**. The number is almost always optimistic (selection bias, in-sample tuning, costless backtesting). Internal walk-forward replication on our own data, with our own cost model, is the only number that should ever influence a promotion decision in this repo.

For each source: read for the *idea* and the *intuition*. Re-derive everything else.
