"""End-to-end smoke test for the paper-order pipeline.

Runs the full gate chain against today's journal:

  1. Write a fake risk-manager APPROVE record.
  2. Write a fake compliance-checker APPROVE record.
  3. Invoke scripts/place_order.py --paper --dry-run (which verifies both gates
     from the journal, writes a submit_dry_run record, and exits 0).
  4. Confirm the submit_dry_run record landed in today's journal.

No real broker call, no live trading. Run with live alpaca paper creds + drop
`--dry-run` to exercise the real PaperBroker → Alpaca path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # so `from src...` works when run as `python scripts/smoke_paper.py`

from src.journal.writer import JournalWriter  # noqa: E402

JOURNAL = REPO / "journal"


def _write_fake_approvals(cycle_id: str) -> None:
    w = JournalWriter(JOURNAL)
    w.write(
        {
            "gate": "risk",
            "decision": "APPROVE",
            "cycle_id": cycle_id,
            "size": 1,
            "reason": "smoke: qty=1 within all caps",
        }
    )
    w.write(
        {
            "gate": "compliance",
            "decision": "APPROVE",
            "cycle_id": cycle_id,
            "reason": "smoke: all checks pass",
        }
    )


def _journal_path_today() -> Path:
    return JOURNAL / f"{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"


def main(live: bool = False) -> int:
    cycle_id = f"smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    _write_fake_approvals(cycle_id)

    cmd = [
        sys.executable,
        str(REPO / "scripts" / "place_order.py"),
        "--paper",
        "--symbol",
        "SPY",
        "--qty",
        "1",
        "--side",
        "buy",
        "--type",
        "market",
        "--cycle-id",
        cycle_id,
    ]
    if not live:
        cmd.append("--dry-run")

    result = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=REPO)
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, end="")
        return result.returncode

    # Verify the submit record actually landed.
    path = _journal_path_today()
    lines = path.read_text().splitlines() if path.exists() else []
    matching = [
        json.loads(line) for line in lines if line and cycle_id in line and ("submit" in line)
    ]
    if not matching:
        print("FAIL: no submit record found for cycle", cycle_id, file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "smoke": "ok",
                "cycle_id": cycle_id,
                "mode": "live-paper" if live else "dry-run",
                "records_written": len(matching),
            }
        )
    )
    return 0


if __name__ == "__main__":
    live_mode = "--live" in sys.argv
    sys.exit(main(live=live_mode))
