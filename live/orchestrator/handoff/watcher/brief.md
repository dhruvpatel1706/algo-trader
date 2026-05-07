# Watcher Handoff Brief — 2026-05-07T02:47 UTC (Cycle 1)

## What I saw
- Bot running, PID 46561, no crashes, heartbeat fresh
- 5 signals/cycle from crypto_agent (ma_pullback_trend_crypto), consistent
- 11 trades submitted in first 3 cycles (02:10–02:24); 0 since due to cash exhaustion
- Refusal rate 100% for last 4 cycles — all broker_rejected (insufficient_balance: .64 free)
- LLM chain fully down: OpenAI 429 quota exceeded; all 31 reasoner evals are fail_open=True
- Watchdog LLM also down; its verdicts are unreliable for LLM-dependent checks

## What I found (INCIDENT)
- **ETHUSD 37.1% of equity (,003 / ,743)** — breaches 30% threshold
- Written to: live/orchestrator/watcher/INCIDENT-20260507T0247.md
- Cause: 3x cumulative ETH buys (4 ETH × 3 cycles) stacked uncapped due to known cumulative-cap gap
- Drawdown: only -0.26%, well below 3% — not a P&L crisis, purely concentration risk
- Stop distances healthy: all positions 4.6–5.0% above stops

## What to watch next cycle
1. **Agent position attribution**: positions API shows agent=null for all 5 positions;
   crypto_agent reports n_open_positions=0. Investigate whether exit signals can fire.
   Read src/agents/crypto_agent.py — does it reconcile its own positions from broker API?
2. **LLM recovery**: has OpenAI quota been restored? Check autonomous_reasoner_eval events
   for fail_open=False. If still down, reasoner is still rubber-stamping.
3. **ETH concentration**: has Operator trimmed ETH? Check if ETHUSD value < ,923 (30%).
4. **Any new trade submissions**: if cash appears (e.g., position close proceeds),
   watch for concentration to worsen on next buy cycle.
5. **Watchdog verdict reliability**: watch for watchdog health= good while fail_open persists —
   that is a false negative we observed in this cycle.

## Key numbers to track
- equity: ,743.66 | cash: .64 | ETHUSD: 15.96 qty @ ,318.53
- Refusal rate since 02:30: 100% (all broker_rejected)
- fail_open_reasoner_count at last watchdog snapshot: 14 (growing every 5-min cycle by +5)
