#!/usr/bin/env bash
# PreToolUse hook on Bash. Polices ONLY scripts/place_order.py invocations.
# All other Bash commands pass straight through (exit 0).
#
# Reads the tool-use JSON event from stdin and writes feedback to stderr.
# Exit codes: 0 = allow, 2 = block (Claude sees the stderr message).
set -euo pipefail

EVENT=$(cat)
CMD=$(printf '%s' "$EVENT" | python3 -c 'import sys, json
try:
    print(json.load(sys.stdin).get("tool_input", {}).get("command", ""))
except Exception:
    print("")
' 2>/dev/null || echo "")

# Only police place_order.py invocations. Everything else passes through.
if [[ "$CMD" != *"scripts/place_order.py"* ]]; then
  exit 0
fi

# v1 policy: paper only, always.
if [[ "${LIVE_TRADING:-0}" == "1" ]]; then
  echo "BLOCKED: LIVE_TRADING=1 is set. v1 does not permit live orders. Unset LIVE_TRADING and retry." >&2
  exit 2
fi

if [[ "$CMD" != *"--paper"* ]]; then
  echo "BLOCKED: scripts/place_order.py must be invoked with --paper in v1." >&2
  exit 2
fi

# Require a same-day APPROVE record from both gates in the journal.
TODAY=$(date -u +%F)
JOURNAL="${CLAUDE_PROJECT_DIR:-$(pwd)}/journal/${TODAY}.jsonl"
if [[ ! -f "$JOURNAL" ]]; then
  echo "BLOCKED: no journal at $JOURNAL. Run the research cycle (which writes a JSONL record via the gates) before placing orders." >&2
  exit 2
fi

# Look at the most recent ~50 records for both APPROVEs.
RECENT=$(tail -n 50 "$JOURNAL")
if ! grep -E '"gate"\s*:\s*"risk".*"decision"\s*:\s*"APPROVE"' <<<"$RECENT" >/dev/null; then
  echo "BLOCKED: most recent journal entries are missing a risk-manager APPROVE." >&2
  exit 2
fi
if ! grep -E '"gate"\s*:\s*"compliance".*"decision"\s*:\s*"APPROVE"' <<<"$RECENT" >/dev/null; then
  echo "BLOCKED: most recent journal entries are missing a compliance-checker APPROVE." >&2
  exit 2
fi

exit 0
