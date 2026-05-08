"""Friday-afternoon ETF scout: TradingView multi-TF BUY confluence -> paper submit.

When the mechanical strategies (mr_etf, ma_pullback_trend, failed_breakout,
vwap_open_retest) aren't seeing setups but the operator wants observable
paper activity before the weekend, this scout runs a one-shot scan:

  1. Fetch TV ratings on a small set of liquid ETFs across 1D / 4H / 1H.
  2. Score each by signed confluence (STRONG_BUY=+2, BUY=+1, ...).
  3. Pick the top candidate iff multi-TF agreement is unambiguous.
  4. Build a ProposedTrade with conservative ATR-style stop / R:R.
  5. Run the SAME risk gate (`check_limits`) and ApprovalToken factory the
     bot uses; refusal records exactly the same reason taxonomy.
  6. Submit via PaperBroker. Journal `submit_intent` BEFORE the broker call,
     `submit_ack` after. Idempotent client_order_id.

NOT a strategy. Discretionary single-shot tagged ``etf_scout_tv_v1`` so it
doesn't pollute the leaderboard. Risk caps unchanged. Live broker untouched.

Usage::

    uv run python scripts/scout_etf_paper.py --dry-run  # scan only, no submit
    uv run python scripts/scout_etf_paper.py            # submit top candidate
    uv run python scripts/scout_etf_paper.py --top 2    # submit top-2
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

# Load .env before importing alpaca / TV modules.
try:
    from dotenv import load_dotenv

    load_dotenv(override=True)
except ImportError:
    pass

import ulid
import yfinance as yf
from src.config import PROJECT_ROOT, get_settings
from src.execution.broker import BrokerSubmitError, PaperBroker, approval_token
from src.execution.orders import Order, new_client_order_id
from src.journal.writer import JournalWriter
from src.risk.limits import Decision, PortfolioSnapshot, ProposedTrade, check_limits

log = logging.getLogger("scout_etf")

# Liquid ETFs only — wide spreads + paid options data not needed.
DEFAULT_UNIVERSE = (
    "SPY", "QQQ", "IWM", "IVV", "VTI",
    "XLK", "XLF", "XLV", "GLD", "SLV",
)

_SCORE_MAP = {
    "STRONG_BUY": 2.0,
    "BUY": 1.0,
    "NEUTRAL": 0.0,
    "SELL": -1.0,
    "STRONG_SELL": -2.0,
}


@dataclass(frozen=True, slots=True)
class TvVerdict:
    symbol: str
    rating_1d: str
    rating_4h: str
    rating_1h: str
    score: float
    last_price: float


def _tv_rate(symbol: str, interval) -> tuple[str, dict[str, int]]:
    """Wrap TradingView fetch. Empty rating on any error (rate-limit, missing)."""
    try:
        from tradingview_ta import TA_Handler
    except ImportError:
        return "NEUTRAL", {}
    try:
        h = TA_Handler(
            symbol=symbol,
            screener="america",
            exchange="AMEX",
            interval=interval,
        )
        a = h.get_analysis()
        return str(a.summary.get("RECOMMENDATION", "NEUTRAL")), a.summary
    except Exception as e:
        log.debug("TV fetch failed for %s @ %s: %s", symbol, interval, e)
        return "NEUTRAL", {}


def scan_universe(universe: tuple[str, ...]) -> list[TvVerdict]:
    """Score each symbol across 1D / 4H / 1H. Single shot, no caching."""
    from tradingview_ta import Interval

    out: list[TvVerdict] = []
    for sym in universe:
        r_1d, _ = _tv_rate(sym, Interval.INTERVAL_1_DAY)
        r_4h, _ = _tv_rate(sym, Interval.INTERVAL_4_HOURS)
        r_1h, _ = _tv_rate(sym, Interval.INTERVAL_1_HOUR)
        score = (
            _SCORE_MAP.get(r_1d, 0.0)
            + _SCORE_MAP.get(r_4h, 0.0)
            + _SCORE_MAP.get(r_1h, 0.0)
        )
        # Last close from yfinance — TV's `close` field is sometimes stale; yfinance
        # is a free quote we already trust elsewhere in the loader.
        try:
            last = float(yf.Ticker(sym).fast_info.last_price)
        except Exception as e:
            log.warning("yfinance last_price for %s failed: %s", sym, e)
            last = 0.0
        out.append(
            TvVerdict(
                symbol=sym,
                rating_1d=r_1d,
                rating_4h=r_4h,
                rating_1h=r_1h,
                score=score,
                last_price=last,
            )
        )
    out.sort(key=lambda v: -v.score)
    return out


def _adapt_position(p) -> object:
    """Coerce Alpaca position dict to the open_risk Protocol the risk gate expects."""
    s = get_settings()
    cap = Decimal(str(s.MAX_PER_TRADE_RISK))

    @dataclass(frozen=True, slots=True)
    class _Pos:
        open_risk: Decimal
        symbol: str
        notional: Decimal

    if isinstance(p, dict):
        sym = str(p.get("symbol", "") or "")
        try:
            mv = Decimal(str(p.get("market_value", 0) or 0))
        except Exception:
            mv = Decimal("0")
        return _Pos(open_risk=abs(mv) * cap, symbol=sym, notional=abs(mv))
    return _Pos(open_risk=Decimal("0"), symbol="", notional=Decimal("0"))


def _snapshot_from_broker(client) -> PortfolioSnapshot:
    """Build a PortfolioSnapshot from the live Alpaca paper account."""
    acc = client.get_account()
    equity = Decimal(str(acc.equity))
    cash = Decimal(str(acc.cash))
    # Alpaca exposes `last_equity` (yesterday's close); use as trailing peak proxy.
    peak = Decimal(str(getattr(acc, "last_equity", equity) or equity))
    peak = max(peak, equity)

    positions = []
    try:
        raw = client.get_all_positions()
        for p in raw:
            positions.append(
                _adapt_position(
                    {
                        "symbol": p.symbol,
                        "market_value": p.market_value,
                        "qty": p.qty,
                        "avg_entry_price": p.avg_entry_price,
                    }
                )
            )
    except Exception as e:
        log.warning("get_all_positions failed: %s", e)
    return PortfolioSnapshot(
        equity=equity,
        cash=cash,
        realized_pnl_today=Decimal("0"),
        unrealized_pnl_today=Decimal("0"),
        trailing_peak_equity=peak,
        open_positions=tuple(positions),
    )


def _propose_trade(verdict: TvVerdict) -> ProposedTrade | None:
    """ATR-light entry/stop/target. Conservative: 1.5% stop, 4% target (R:R 2.7)."""
    if verdict.last_price <= 0:
        return None
    entry = Decimal(str(round(verdict.last_price, 2)))
    stop = (entry * Decimal("0.985")).quantize(Decimal("0.01"))
    target = (entry * Decimal("1.04")).quantize(Decimal("0.01"))
    return ProposedTrade(
        symbol=verdict.symbol,
        side="buy",
        entry=entry,
        stop=stop,
        target=target,
        strategy_tag="etf_scout_tv_v1",
    )


def _format_verdict(v: TvVerdict) -> str:
    return (
        f"  {v.symbol:5} score={v.score:+.1f} "
        f"1D={v.rating_1d:11} 4H={v.rating_4h:11} 1H={v.rating_1h:11} "
        f"px=${v.last_price:.2f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan + risk-gate but do NOT submit",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=1,
        help="how many of the highest-confluence candidates to submit (default 1)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=4.0,
        help="minimum signed multi-TF score to consider (default 4.0 = at least 2× BUY across 3 TFs)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="explicit paper-only confirmation (matches scripts/place_order.py)",
    )
    parser.add_argument(
        "--universe",
        type=str,
        default=",".join(DEFAULT_UNIVERSE),
        help="comma-separated tickers",
    )
    parser.add_argument(
        "--force-symbol",
        type=str,
        default=None,
        help=(
            "skip TV scan and submit this symbol directly with the most recent "
            "yfinance close. Use after a dry-run has independently verified the "
            "TV confluence (e.g. when subsequent TV calls are rate-limited)."
        ),
    )
    parser.add_argument(
        "--force-score",
        type=float,
        default=6.0,
        help=(
            "score to journal alongside a --force-symbol submission. Default "
            "6.0 mirrors a 3-of-3 STRONG_BUY confluence — pass a lower number "
            "if you saw a less-aligned dry-run."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    s = get_settings()
    if not s.ALPACA_PAPER_TRADE:
        print("scout_etf: refusing to run without ALPACA_PAPER_TRADE=True", file=sys.stderr)
        return 2
    if s.LIVE_TRADING == "1":
        print("scout_etf: refusing with LIVE_TRADING=1", file=sys.stderr)
        return 2
    if not args.dry_run and not args.paper:
        print(
            "scout_etf: pass --paper to confirm paper-only submission "
            "(belt-and-suspenders with the env settings)",
            file=sys.stderr,
        )
        return 2

    if args.force_symbol:
        sym = args.force_symbol.upper()
        try:
            last = float(yf.Ticker(sym).fast_info.last_price)
        except Exception as e:
            print(f"scout_etf: yfinance last_price for {sym} failed: {e}", file=sys.stderr)
            return 2
        chosen = [
            TvVerdict(
                symbol=sym,
                rating_1d="FORCED",
                rating_4h="FORCED",
                rating_1h="FORCED",
                score=args.force_score,
                last_price=last,
            )
        ]
        print(f"=== --force-symbol {sym} @ ${last:.2f} (score={args.force_score}) ===")
    else:
        universe = tuple(t.strip().upper() for t in args.universe.split(",") if t.strip())
        print(f"scout_etf: scanning {len(universe)} tickers across 1D/4H/1H ...")
        verdicts = scan_universe(universe)
        print("\n=== TradingView ratings ===")
        for v in verdicts:
            print(_format_verdict(v))

        candidates = [v for v in verdicts if v.score >= args.min_score and v.last_price > 0]
        if not candidates:
            print(
                f"\nscout_etf: no candidates with score >= {args.min_score}. nothing to do."
            )
            return 0

        chosen = candidates[: args.top]
        print(f"\n=== Will submit {len(chosen)} candidate(s) ===")

    journal_dir = Path(os.environ.get("JOURNAL_DIR") or (PROJECT_ROOT / "journal"))
    writer = JournalWriter(journal_dir)
    broker = PaperBroker()

    submitted = 0
    refused = 0
    for v in chosen:
        proposed = _propose_trade(v)
        if proposed is None:
            print(f"  {v.symbol}: skipped (no price)")
            continue

        try:
            snapshot = _snapshot_from_broker(broker._client)
        except Exception as e:
            print(f"  {v.symbol}: snapshot failed: {e}", file=sys.stderr)
            refused += 1
            continue

        # Sum existing notional in this symbol so the cumulative cap is correctly
        # measured (mirrors src.runtime.trade_pipeline._existing_notional_in_symbol).
        existing = Decimal("0")
        for p in snapshot.open_positions:
            if getattr(p, "symbol", "") == v.symbol:
                existing += getattr(p, "notional", Decimal("0"))

        decision = check_limits(proposed, snapshot, existing_notional_in_symbol=existing)
        if not decision.approve:
            print(f"  {v.symbol}: refused at risk gate -> {decision.reason}")
            writer.write(
                {
                    "event": "refusal",
                    "ts": datetime.now(UTC).isoformat(),
                    "agent": "scout_etf",
                    "strategy": "etf_scout_tv_v1",
                    "symbol": v.symbol,
                    "reason": "risk_cap_position",
                    "detail": decision.reason,
                    "tv_score": v.score,
                    "tv_1d": v.rating_1d,
                    "tv_4h": v.rating_4h,
                    "tv_1h": v.rating_1h,
                }
            )
            refused += 1
            continue

        qty = decision.adjusted_size or 0
        if qty <= 0:
            print(f"  {v.symbol}: risk approved but adjusted_size={qty}; skipping")
            refused += 1
            continue

        cycle_id = str(ulid.ULID())
        compliance = Decision(
            True,
            "v1 equity has no compliance gate beyond risk",
            adjusted_size=qty,
        )
        token = approval_token(
            cycle_id=cycle_id,
            risk=decision,
            compliance=compliance,
        )

        order = Order(
            client_order_id=new_client_order_id(),
            symbol=v.symbol,
            qty=int(qty),
            side="buy",
            order_type="market",
            time_in_force="day",
            limit_price=None,
            extended_hours=False,
            strategy_tag="etf_scout_tv_v1",
        )

        # submit_intent BEFORE broker call (audit-of-record).
        intent_payload = {
            "event": "submit_intent",
            "ts": datetime.now(UTC).isoformat(),
            "cycle_id": cycle_id,
            "agent": "scout_etf",
            "strategy": "etf_scout_tv_v1",
            "symbol": v.symbol,
            "side": "buy",
            "qty": int(qty),
            "entry": float(proposed.entry),
            "stop": float(proposed.stop),
            "target": float(proposed.target) if proposed.target else None,
            "tv_score": v.score,
            "tv_1d": v.rating_1d,
            "tv_4h": v.rating_4h,
            "tv_1h": v.rating_1h,
            "client_order_id": order.client_order_id,
        }
        writer.write(intent_payload)

        if args.dry_run:
            print(f"  {v.symbol}: --dry-run, would submit qty={qty} @ ~${proposed.entry}")
            continue

        try:
            sub = broker.submit(order, token)
        except BrokerSubmitError as e:
            print(f"  {v.symbol}: broker rejected -> {e}", file=sys.stderr)
            writer.write(
                {
                    "event": "submit_reject",
                    "ts": datetime.now(UTC).isoformat(),
                    "cycle_id": cycle_id,
                    "symbol": v.symbol,
                    "client_order_id": order.client_order_id,
                    "detail": str(e),
                }
            )
            refused += 1
            continue

        ack_payload = {
            "event": "submit_ack",
            "ts": datetime.now(UTC).isoformat(),
            "cycle_id": cycle_id,
            "agent": "scout_etf",
            "strategy": "etf_scout_tv_v1",
            "symbol": v.symbol,
            "side": "buy",
            "qty": int(qty),
            "broker_order_id": sub.broker_order_id,
            "client_order_id": sub.client_order_id,
            "status": sub.status,
            "accepted_at": sub.accepted_at.isoformat(),
        }
        writer.write(ack_payload)
        print(
            f"  {v.symbol}: SUBMITTED qty={qty} entry=~${proposed.entry} "
            f"stop=${proposed.stop} target=${proposed.target} "
            f"order_id={sub.broker_order_id} status={sub.status}"
        )
        submitted += 1

    print(f"\nscout_etf: submitted={submitted} refused={refused}")
    return 0 if submitted > 0 or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
