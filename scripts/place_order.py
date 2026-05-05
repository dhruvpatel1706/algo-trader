"""Single entry point for placing a paper order. The PreToolUse hook guards this script.

Verifies:
  - `ALPACA_PAPER_TRADE=True` and `--paper` were both supplied.
  - Today's journal contains a recent risk APPROVE and compliance APPROVE
    that match the supplied `--cycle-id` and were written within the
    `--approval-max-age-sec` window (default 300s).
  - All Order fields are valid.
  - Both gates are re-validated by ``approval_token()`` before submit.
  - A ``submit_intent`` journal record is written + fsync'd BEFORE the broker
    call, so a process crash mid-submit still leaves an audit trail of intent.

In `--dry-run` mode: writes a journal record but does NOT submit to the broker.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.config import get_settings
from src.execution.broker import PaperBroker, approval_token
from src.execution.orders import Order, new_client_order_id
from src.journal.writer import JournalWriter
from src.risk.limits import Decision

_DEFAULT_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_APPROVAL_MAX_AGE = 300  # seconds — approval freshness window


def _publish(channel: str, payload: dict) -> None:
    """Fire-and-forget Redis publish. Never raises — the dashboard is best-effort."""
    try:
        import redis

        client = redis.Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=1)
        client.publish(channel, json.dumps(payload, default=str))
    except Exception:
        pass


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Place a paper order via Alpaca.")
    p.add_argument(
        "--paper", action="store_true", required=True, help="paper-only switch (required in v1)"
    )
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", type=int, required=True)
    p.add_argument("--side", choices=["buy", "sell"], required=True)
    p.add_argument("--type", dest="order_type", choices=["market", "limit"], default="market")
    p.add_argument("--price", type=Decimal, default=None, help="limit price")
    p.add_argument("--tif", choices=["day", "gtc", "ioc", "fok"], default="day")
    p.add_argument("--client-order-id", default=None)
    p.add_argument("--strategy-tag", default="")
    p.add_argument(
        "--cycle-id",
        required=True,
        help=(
            "evaluation cycle ID. Approval records in the journal MUST match this "
            "cycle_id — old approvals from earlier cycles cannot authorize a new "
            "order."
        ),
    )
    p.add_argument(
        "--dry-run", action="store_true", help="verify gates and journal but do not submit"
    )
    p.add_argument(
        "--approval-max-age-sec",
        type=int,
        default=_DEFAULT_APPROVAL_MAX_AGE,
        help=(
            "Maximum age (seconds) of an approval record relative to now. Approvals "
            "older than this window are ignored even if they match cycle_id. "
            "Defaults to 300s — the cycle period of the runner is 5 min for equity."
        ),
    )
    p.add_argument(
        "--repo-root",
        default=str(_DEFAULT_REPO),
        help=(
            "override the journal lookup root. Tests use this to run against an "
            "isolated journal; production callers omit it and the script uses the "
            "repo containing this script."
        ),
    )
    return p.parse_args()


def _today_journal(journal: Path) -> Path:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return journal / f"{today}.jsonl"


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        # `JournalWriter` writes UTC-aware ISO timestamps.
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


def _latest_approvals(
    journal: Path,
    cycle_id: str,
    *,
    max_age: timedelta,
    now: datetime | None = None,
) -> tuple[Decision, Decision] | None:
    """Find risk + compliance APPROVE records for ``cycle_id`` within ``max_age``.

    Approvals must:
      - Carry the exact ``cycle_id`` of the order being submitted.
      - Be at most ``max_age`` old relative to ``now`` (default = utcnow).

    Returns the latest approving pair, or ``None`` if either is missing/stale.
    """
    path = _today_journal(journal)
    if not path.exists():
        return None
    cutoff = (now or datetime.now(UTC)) - max_age
    risk: Decision | None = None
    compliance: Decision | None = None
    for line in path.read_text(encoding="utf-8").splitlines()[-1000:]:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("cycle_id") != cycle_id or r.get("decision") != "APPROVE":
            continue
        ts = _parse_ts(r.get("ts"))
        if ts is None or ts < cutoff:
            continue
        if r.get("gate") == "risk":
            risk = Decision(True, r.get("reason", "approved"), adjusted_size=r.get("size"))
        elif r.get("gate") == "compliance":
            compliance = Decision(True, r.get("reason", "approved"))
    if risk and compliance:
        return risk, compliance
    return None


def main() -> int:
    args = _parse_args()
    settings = get_settings()

    if not args.paper:
        print("ERROR: --paper is required in v1.", file=sys.stderr)
        return 2
    if not settings.ALPACA_PAPER_TRADE:
        print("ERROR: ALPACA_PAPER_TRADE is not True in env.", file=sys.stderr)
        return 2
    if settings.LIVE_TRADING == "1":
        print("ERROR: LIVE_TRADING=1 is set. v1 does not permit live orders.", file=sys.stderr)
        return 2

    journal_dir = Path(args.repo_root) / "journal"
    pair = _latest_approvals(
        journal_dir,
        args.cycle_id,
        max_age=timedelta(seconds=args.approval_max_age_sec),
    )
    if pair is None:
        print(
            "ERROR: today's journal has no fresh risk + compliance APPROVE pair for "
            f"cycle_id={args.cycle_id} within the last {args.approval_max_age_sec}s.",
            file=sys.stderr,
        )
        return 2
    risk_dec, comp_dec = pair

    coid = args.client_order_id or new_client_order_id()

    order = Order(
        client_order_id=coid,
        symbol=args.symbol.upper(),
        qty=args.qty,
        side=args.side,
        order_type=args.order_type,
        time_in_force=args.tif,
        limit_price=args.price,
        strategy_tag=args.strategy_tag,
    )
    # Factory re-validates both gates and raises on any APPROVE-bypass attempt.
    token = approval_token(args.cycle_id, risk_dec, comp_dec)

    writer = JournalWriter(journal_dir)
    if args.dry_run:
        record = {
            "event": "submit_dry_run",
            "cycle_id": args.cycle_id,
            "client_order_id": coid,
            "symbol": order.symbol,
            "qty": order.qty,
            "side": order.side,
            "order_type": order.order_type,
            "limit_price": str(order.limit_price) if order.limit_price else None,
            "approvals": {"risk": True, "compliance": True},
        }
        writer.write(record)
        _publish("order.submit_dry_run", record)
        print(json.dumps({"event": "submit_dry_run", "client_order_id": coid, "ok": True}))
        return 0

    # Write `submit_intent` BEFORE touching the broker. Crash-safe: if the
    # process dies between this fsync and the broker call, the journal
    # records intent but no broker_order_id; recovery flags it as orphan
    # rather than missing it entirely.
    intent_record = {
        "event": "submit_intent",
        "cycle_id": args.cycle_id,
        "client_order_id": coid,
        "symbol": order.symbol,
        "qty": order.qty,
        "side": order.side,
        "order_type": order.order_type,
        "limit_price": str(order.limit_price) if order.limit_price else None,
        "approvals": {"risk": True, "compliance": True},
    }
    writer.write(intent_record)

    broker = PaperBroker()
    submission = broker.submit(order, token)
    ack_record = {
        "event": "submit_ack",
        "cycle_id": args.cycle_id,
        "client_order_id": submission.client_order_id,
        "broker_order_id": submission.broker_order_id,
        "symbol": order.symbol,
        "qty": order.qty,
        "side": order.side,
        "order_type": order.order_type,
        "limit_price": str(order.limit_price) if order.limit_price else None,
        "status": submission.status,
        "approvals": {"risk": True, "compliance": True},
    }
    writer.write(ack_record)
    _publish("order.submit", ack_record)
    print(json.dumps({"event": "submit", "client_order_id": coid, "status": submission.status}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
