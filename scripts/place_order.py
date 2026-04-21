"""Single entry point for placing a paper order. The PreToolUse hook guards this script.

Verifies:
  - `ALPACA_PAPER_TRADE=True` and `--paper` were both supplied.
  - Today's journal contains a recent risk APPROVE and compliance APPROVE.
  - All Order fields are valid.

In `--dry-run` mode: writes a journal record but does NOT submit to the broker.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from src.config import get_settings
from src.execution.broker import ApprovalToken, PaperBroker
from src.execution.orders import Order, new_client_order_id
from src.journal.writer import JournalWriter
from src.risk.limits import Decision

REPO = Path(__file__).resolve().parent.parent
JOURNAL = REPO / "journal"


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
    p.add_argument("--cycle-id", default=None)
    p.add_argument(
        "--dry-run", action="store_true", help="verify gates and journal but do not submit"
    )
    return p.parse_args()


def _today_journal() -> Path:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return JOURNAL / f"{today}.jsonl"


def _latest_approvals() -> tuple[Decision, Decision] | None:
    path = _today_journal()
    if not path.exists():
        return None
    risk: Decision | None = None
    compliance: Decision | None = None
    for line in path.read_text(encoding="utf-8").splitlines()[-200:]:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("gate") == "risk" and r.get("decision") == "APPROVE":
            risk = Decision(True, r.get("reason", "approved"), adjusted_size=r.get("size"))
        elif r.get("gate") == "compliance" and r.get("decision") == "APPROVE":
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

    pair = _latest_approvals()
    if pair is None:
        print(
            "ERROR: today's journal is missing a recent risk-manager + compliance-checker APPROVE.",
            file=sys.stderr,
        )
        return 2
    risk_dec, comp_dec = pair

    cycle_id = args.cycle_id or "manual"
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
    token = ApprovalToken(
        cycle_id=cycle_id,
        risk_decision_ts=datetime.now(UTC),
        compliance_decision_ts=datetime.now(UTC),
        risk_reason=risk_dec.reason,
        compliance_reason=comp_dec.reason,
    )

    writer = JournalWriter(JOURNAL)
    if args.dry_run:
        record = {
            "event": "submit_dry_run",
            "cycle_id": cycle_id,
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

    broker = PaperBroker()
    submission = broker.submit(order, token)
    record = {
        "event": "submit",
        "cycle_id": cycle_id,
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
    writer.write(record)
    _publish("order.submit", record)
    print(json.dumps({"event": "submit", "client_order_id": coid, "status": submission.status}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
