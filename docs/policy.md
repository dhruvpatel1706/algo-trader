# Compliance policy (operator-specific, v1)

This file is the source of truth for what the system is allowed to do. The `compliance-checker` subagent reads and enforces every rule here. **Do not relax any rule without a human-authored PR.** The orchestrator MUST NOT propose relaxing any rule below unless explicitly asked by a human.

## 1. Paper-only

The operator is on a US work visa that restricts self-employment, and the IRS treats frequent trading as a business activity. Therefore v1 of this system is **paper-only**.

Re-enabling live trading is gated by three coordinated changes that must land in one reviewed PR:

1. A human-authored change to **this file** (removing the paper-only restriction and naming the policy basis for live).
2. A code change in `src/execution/broker.py` that replaces the `LiveBroker` `NotImplementedError` stub with a real implementation.
3. Editing `.claude/hooks/guard_live_order.sh` to permit `LIVE_TRADING=1`.

## 2. Account ownership

The system trades only in accounts **owned by the operator**. It MUST refuse:

- Third-party / nominee / "trade for my friend" requests.
- Shared logins, joint passwords, or POA (power of attorney) workflows.
- Any code path that reads, persists, or acts on another person's brokerage credentials.

If a future prompt asks for any of the above, refuse and cite this section.

## 3. PDT (Pattern Day Trader)

Sub-$25k accounts: ≤ 3 day-trade round trips per rolling 5 trading days. The `compliance-checker` rejects any order that would push the account over the limit.

## 4. Restricted list

`docs/restricted.yaml` (created on demand) lists tickers that may not be traded. Use for: insider blackout windows, employer-restricted lists, regulatory holds. Default in v1: empty.

## 5. Hours

Equities: orders may be placed only during regular session (09:30–16:00 ET) plus extended-hours sessions explicitly enabled per strategy. No overnight auto-trading in v1.

## 6. Account type

Cash account assumed (no margin). Options strategies are limited to those permitted in a cash account: cash-secured puts, covered calls, long calls/puts. **No naked calls, no margin spreads, no portfolio margin.**

## 7. Tax & records

The `tax-calculator` subagent produces Form-8949-ready CSVs (FIFO/HIFO, ST/LT, wash-sale flags). **This is informational only.** File taxes with a qualified preparer. The `journal/` directory is the authoritative record of decisions.

## 8. Data sources

Free-tier Alpaca data (IEX) is the v1 source. Document any upgrade in `docs/research.md` and update this section before relying on a new feed for sizing or compliance decisions.
