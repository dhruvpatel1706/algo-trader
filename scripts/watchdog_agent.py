"""LLM-driven watchdog agent — the bot's autonomous nurse.

This is a *separate* process from the runner. Sibling to ``scripts/watchdog.py``
(which is the simple heartbeat dead-man switch). Where the dead-man flattens on
no-pulse, this agent reads the journal + log + API status, summarizes the bot's
behavior, and emits structured health verdicts. When an LLM key is available,
it asks Gemini for a structured diagnosis and recommended actions; without a
key it falls back to mechanical health metrics.

What it does
------------
Every ``--interval-seconds`` (default 300s):

1. Pulls ``GET /api/bot/status`` from the dashboard backend.
2. Reads the tail of ``live/runtime/runner.log``.
3. Reads today's ``live/journal/YYYY-MM-DD.jsonl``.
4. Computes a deterministic health summary (crash counts, refusal rate, eval
   counts per agent, fail-open count from the autonomous reasoner).
5. If ``GEMINI_API_KEY`` (or one of the other supported keys) is set, asks
   Claude/Gemini for a structured JSON verdict:
   ``{"health": "good"|"degraded"|"critical", "concerns": [...], "actions": [
       {"type": "restart_bot"|"halt_strategy"|"none", "target": "...",
        "reason": "..."}]}``.
6. Auto-executes only the explicitly-allow-listed actions (start/restart on
   crash). Everything else is logged as an advisory and surfaced to the
   operator via the watchdog journal — never touches the broker, never edits
   .env, never modifies risk caps.
7. Persists the analysis as one line of JSONL at
   ``live/watchdog/agent_YYYY-MM-DD.jsonl``.

What it does NOT do
-------------------
- Place orders. The watchdog has no broker handle; the only mutating action
  it can take is hitting the runner-control endpoints exposed by the dashboard.
- Modify code, .env, or risk caps. Those are coordinated PR territory.
- Bypass the dead-man watchdog. ``scripts/watchdog.py`` still owns the
  flatten-on-no-pulse behavior; this agent is a quality monitor, not a
  safety floor.

CLI::

    uv run python scripts/watchdog_agent.py [--interval-seconds 300] [--once]

Designed to run alongside the runner under launchd / systemd. The watchdog
agent crashing must NOT take the runner down (and vice versa); they share
nothing but the journal directory and the dashboard API.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from src.config import PROJECT_ROOT

log = logging.getLogger("algo_trader.watchdog_agent")

_DEFAULT_API_BASE = os.environ.get("DASHBOARD_API_BASE", "http://localhost:8000")
# Canonical journal location matches dashboard/api/journal_reader.py and
# scripts/run_bot.py — see run_bot.build_runner for the rationale.
_JOURNAL_DIR = PROJECT_ROOT / "journal"
_RUNNER_LOG = PROJECT_ROOT / "live" / "runtime" / "runner.log"
_WATCHDOG_DIR = PROJECT_ROOT / "live" / "watchdog"

# Allow-list of action types the watchdog will auto-execute. Anything else
# is logged as advisory and shown to the operator via the watchdog journal.
_AUTO_ACTIONS: frozenset[str] = frozenset({"restart_bot"})

# How long the bot can sit in "crashed" state before the watchdog restarts it.
# Short enough that overnight runs recover quickly; long enough that a busy
# operator clicking Stop manually doesn't fight the watchdog.
_CRASH_RESTART_GRACE_SEC = 60.0

HealthLevel = Literal["good", "degraded", "critical", "unknown"]


@dataclass(frozen=True)
class HealthSnapshot:
    """Mechanical health metrics computed from journal + log + status."""

    ts: str
    bot_state: str
    bot_pid: int | None
    bot_uptime_sec: float | None
    bot_exit_code: int | None
    n_journal_events: int
    crash_count_today: int
    eval_count_per_agent: dict[str, int]
    refusal_count: int
    submitted_count: int
    fail_open_reasoner_count: int
    log_tail_excerpt: list[str]
    last_journal_event_age_sec: float | None


@dataclass
class WatchdogVerdict:
    """The watchdog's diagnosis for one cycle."""

    ts: str
    snapshot: HealthSnapshot
    health: HealthLevel
    concerns: list[str] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    auto_executed: list[dict[str, str]] = field(default_factory=list)
    advisory: list[dict[str, str]] = field(default_factory=list)
    llm_provider: str | None = None
    llm_raw: str | None = None
    llm_error: str | None = None


# ---------------------------------------------------------------------------
# Inputs: status + journal + log
# ---------------------------------------------------------------------------


def _http_get_json(url: str, timeout: float = 5.0) -> Any | None:
    """GET a URL and decode JSON. Returns None on any failure (logged)."""
    if not url.startswith(("http://", "https://")):
        log.warning("watchdog: refusing non-http(s) URL %r", url)
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — scheme-checked above
            data = resp.read().decode("utf-8")
        return json.loads(data)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        log.warning("watchdog: GET %s failed: %r", url, e)
        return None


def _http_post(url: str, timeout: float = 10.0) -> int | None:
    """POST an empty body to URL. Returns HTTP status code or None on failure."""
    if not url.startswith(("http://", "https://")):
        log.warning("watchdog: refusing non-http(s) URL %r", url)
        return None
    req = urllib.request.Request(url, data=b"", method="POST")  # noqa: S310 — scheme-checked above
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — scheme-checked above
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("watchdog: POST %s failed: %r", url, e)
        return None


def _read_today_journal(today: date) -> list[dict[str, Any]]:
    """Load today's JSONL events. Returns [] if file missing/unreadable."""
    path = _JOURNAL_DIR / f"{today.isoformat()}.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log.warning("watchdog: failed reading %s: %r", path, e)
    return out


def _read_log_tail(n: int = 80) -> list[str]:
    """Last ``n`` lines of the runner log, or [] if absent."""
    if not _RUNNER_LOG.exists():
        return []
    try:
        # Bounded read: cap at 256 KiB to keep watchdog cheap.
        size = _RUNNER_LOG.stat().st_size
        with _RUNNER_LOG.open("rb") as fh:
            if size > 256 * 1024:
                fh.seek(-256 * 1024, os.SEEK_END)
                fh.readline()  # discard partial leading line
            data = fh.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        return lines[-n:]
    except OSError:
        return []


# ---------------------------------------------------------------------------
# Mechanical health computation
# ---------------------------------------------------------------------------


def _count_today_crashes(events: Iterable[dict[str, Any]]) -> int:
    """Watchdog's notion of a 'crash today' = log line we recognize as such.

    The runner doesn't journal crashes itself (it's how it crashed). Best
    proxy: count distinct ``crashed`` transitions reported by the runner
    supervisor's ``state`` field in any embedded status echo, plus log lines
    matching common crash markers.
    """
    return sum(1 for e in events if e.get("event") == "runner_crashed")


def _build_snapshot(api_base: str) -> HealthSnapshot:
    """Compute the mechanical health snapshot for this cycle."""
    today = datetime.now(UTC).date()
    status = _http_get_json(f"{api_base}/api/bot/status") or {}
    events = _read_today_journal(today)
    log_tail = _read_log_tail(40)

    eval_counts: Counter[str] = Counter()
    refusal_count = 0
    submitted_count = 0
    fail_open_count = 0
    last_event_age: float | None = None
    if events:
        latest_ts = max(
            (e.get("ts") for e in events if isinstance(e.get("ts"), str)),
            default=None,
        )
        if latest_ts:
            try:
                last_dt = datetime.fromisoformat(latest_ts)
                last_event_age = (
                    datetime.now(UTC) - last_dt
                ).total_seconds()
            except ValueError:
                last_event_age = None

    for e in events:
        ev = e.get("event")
        if ev == "agent_eval_complete":
            agent = str(e.get("agent", "unknown"))
            eval_counts[agent] += 1
            refusal_count += int(e.get("n_refused", 0) or 0)
            submitted_count += int(e.get("n_submitted", 0) or 0)
        elif ev == "autonomous_reasoner_eval":
            judgment = e.get("judgment") or {}
            if judgment.get("fail_open") is True:
                fail_open_count += 1

    return HealthSnapshot(
        ts=datetime.now(UTC).isoformat(),
        bot_state=str(status.get("state", "unknown")),
        bot_pid=status.get("pid"),
        bot_uptime_sec=status.get("uptime_sec"),
        bot_exit_code=status.get("exit_code"),
        n_journal_events=len(events),
        crash_count_today=_count_today_crashes(events),
        eval_count_per_agent=dict(eval_counts),
        refusal_count=refusal_count,
        submitted_count=submitted_count,
        fail_open_reasoner_count=fail_open_count,
        log_tail_excerpt=log_tail[-15:],
        last_journal_event_age_sec=last_event_age,
    )


def _classify_health(snap: HealthSnapshot) -> tuple[HealthLevel, list[str]]:
    """Deterministic health classifier. Used as a baseline + LLM fallback.

    Conservative thresholds — we'd rather flag a non-issue than miss a real
    fault during an overnight run.
    """
    concerns: list[str] = []
    level: HealthLevel = "good"

    if snap.bot_state == "crashed":
        concerns.append(f"bot crashed (exit_code={snap.bot_exit_code})")
        level = "critical"
    elif snap.bot_state == "stopped":
        concerns.append("bot stopped (operator-initiated or never started)")
        level = "degraded"

    # Liveness: if the bot says it's running but no events have appeared in
    # 30+ minutes, something is wrong (heartbeat would normally tick every 15s).
    if snap.bot_state == "running" and snap.last_journal_event_age_sec is not None:
        if snap.last_journal_event_age_sec > 1800:
            concerns.append(
                f"no journal events in {int(snap.last_journal_event_age_sec)}s (>30m)"
            )
            level = "critical"
        elif snap.last_journal_event_age_sec > 600:
            concerns.append(
                f"journal slowing — last event {int(snap.last_journal_event_age_sec)}s ago"
            )
            level = "degraded" if level == "good" else level

    # If we've been seeing reasoner fail-opens en masse, the LLM router is
    # probably down — not a hard fault but worth flagging.
    if snap.fail_open_reasoner_count > 10:
        concerns.append(
            f"reasoner fail-open count high ({snap.fail_open_reasoner_count}); "
            "LLM provider chain may be degraded"
        )
        level = "degraded" if level == "good" else level

    # Refusal-rate sanity check. If the bot fired N signals and refused all of
    # them, the risk gate is doing its job — but if it persists for hours it
    # may indicate a sizing / cap misconfig.
    total_signals = snap.refusal_count + snap.submitted_count
    if total_signals >= 20 and snap.submitted_count == 0:
        concerns.append(
            f"all {total_signals} signals refused — risk gate possibly misconfigured"
        )
        level = "degraded" if level == "good" else level

    if not concerns:
        concerns.append("nominal — no anomalies detected")
    return level, concerns


# ---------------------------------------------------------------------------
# Optional LLM analysis layer
# ---------------------------------------------------------------------------


def _llm_analyze(
    snap: HealthSnapshot, fallback_health: HealthLevel
) -> tuple[HealthLevel | None, list[str], list[dict[str, str]], str | None, str | None]:
    """Best-effort LLM diagnosis. Returns (health, concerns, actions, raw, provider).

    Returns ``(None, [], [], None, None)`` if no provider is configured or
    the call fails — caller should fall back to the deterministic verdict.
    """
    try:
        from src.llm.router import LLMUnavailableError, default_router
    except ImportError:
        return None, [], [], None, None

    prompt = _build_llm_prompt(snap, fallback_health)
    try:
        response = default_router().call(
            system="You are a watchdog for a paper-trading bot. Reply with JSON ONLY.",
            user=prompt,
            max_tokens=400,
            temperature=0.0,
        )
    except LLMUnavailableError as e:
        return None, [], [], None, f"router.call: all providers unavailable ({e})"
    except Exception as e:
        return None, [], [], None, f"router.call raised: {e!r}"

    raw = response.text.strip() if hasattr(response, "text") else str(response)
    provider = getattr(response, "provider", None)

    parsed = _safe_parse_llm_json(raw)
    if parsed is None:
        return None, [], [], raw, f"LLM response not parseable as JSON: {raw[:200]!r}"

    health = parsed.get("health") if isinstance(parsed, dict) else None
    concerns = parsed.get("concerns", []) if isinstance(parsed, dict) else []
    actions = parsed.get("actions", []) if isinstance(parsed, dict) else []
    if health not in ("good", "degraded", "critical"):
        health = None
    if not isinstance(concerns, list):
        concerns = []
    if not isinstance(actions, list):
        actions = []

    # Sanitize action shape — never trust LLM with unstructured input.
    safe_actions: list[dict[str, str]] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        safe_actions.append(
            {
                "type": str(a.get("type", "none"))[:32],
                "target": str(a.get("target", ""))[:64],
                "reason": str(a.get("reason", ""))[:200],
            }
        )

    return health, [str(c)[:200] for c in concerns], safe_actions, raw, provider


def _build_llm_prompt(snap: HealthSnapshot, fallback_health: HealthLevel) -> str:
    """Compact, deterministic input the LLM can reason about in 1 turn."""
    return json.dumps(
        {
            "instruction": (
                "Analyze the bot health snapshot. Return JSON: "
                "{\"health\": \"good|degraded|critical\", "
                "\"concerns\": [strings], "
                "\"actions\": [{\"type\": \"restart_bot|halt_strategy|investigate|none\", "
                "\"target\": \"<agent or strategy name>\", \"reason\": \"<text>\"}]}. "
                "Use restart_bot ONLY if state is crashed. Use halt_strategy if a "
                "specific strategy looks degenerate. Use investigate to flag "
                "anomalies you want a human to look at. No prose outside JSON."
            ),
            "deterministic_baseline": fallback_health,
            "snapshot": asdict(snap),
        },
        indent=None,
    )


def _safe_parse_llm_json(text: str) -> Any | None:
    """LLMs sometimes wrap JSON in markdown fences; tolerate that."""
    s = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences if present.
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.endswith("```"):
            s = s[: -len("```")].rstrip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Action execution (allow-listed, never broker-touching)
# ---------------------------------------------------------------------------


def _execute_safe_actions(
    snap: HealthSnapshot, actions: list[dict[str, str]], api_base: str
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Run only the allow-listed actions. Return (executed, advisory)."""
    executed: list[dict[str, str]] = []
    advisory: list[dict[str, str]] = []

    # Mechanical: if the bot is crashed and has been crashed for a while,
    # restart it ourselves regardless of what the LLM said. The LLM may not
    # always emit this; keeping it deterministic protects the overnight run.
    if (
        snap.bot_state == "crashed"
        and (snap.bot_uptime_sec is None or snap.bot_uptime_sec >= _CRASH_RESTART_GRACE_SEC)
    ):
        ok = _restart_bot(api_base)
        executed.append(
            {
                "type": "restart_bot",
                "target": "runner",
                "reason": "deterministic: state=crashed",
                "result": "ok" if ok else "failed",
            }
        )

    # LLM-suggested actions: only the allow-list runs automatically.
    for a in actions:
        atype = a.get("type", "none")
        if atype == "none":
            continue
        if atype == "restart_bot":
            # Skip if we already restarted from the deterministic path above.
            if any(e["type"] == "restart_bot" for e in executed):
                continue
            ok = _restart_bot(api_base)
            executed.append({**a, "result": "ok" if ok else "failed"})
        elif atype in _AUTO_ACTIONS:
            executed.append({**a, "result": "noop_unimplemented"})
        else:
            advisory.append(a)

    return executed, advisory


def _restart_bot(api_base: str) -> bool:
    """POST /api/bot/start — idempotent on the supervisor, safe to spam."""
    code = _http_post(f"{api_base}/api/bot/start")
    return code is not None and 200 <= code < 300


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _persist_verdict(verdict: WatchdogVerdict) -> None:
    """Append the verdict as one JSONL line to live/watchdog/agent_<date>.jsonl."""
    _WATCHDOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).date().isoformat()
    path = _WATCHDOG_DIR / f"agent_{today}.jsonl"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(verdict), default=str))
            fh.write("\n")
    except OSError as e:
        log.warning("watchdog: failed persisting verdict: %r", e)


# ---------------------------------------------------------------------------
# Cycle + main loop
# ---------------------------------------------------------------------------


def run_cycle(api_base: str = _DEFAULT_API_BASE) -> WatchdogVerdict:
    """One full watchdog tick. Returns the verdict (also persisted)."""
    snap = _build_snapshot(api_base)
    base_health, base_concerns = _classify_health(snap)

    llm_health, llm_concerns, llm_actions, llm_raw, llm_meta = _llm_analyze(snap, base_health)
    health: HealthLevel = llm_health or base_health
    concerns = list(dict.fromkeys([*base_concerns, *llm_concerns]))  # dedupe, preserve order

    executed, advisory = _execute_safe_actions(snap, llm_actions, api_base)

    verdict = WatchdogVerdict(
        ts=datetime.now(UTC).isoformat(),
        snapshot=snap,
        health=health,
        concerns=concerns,
        actions=llm_actions,
        auto_executed=executed,
        advisory=advisory,
        llm_provider=llm_meta if llm_meta and not llm_meta.startswith("router") else None,
        llm_raw=llm_raw,
        llm_error=llm_meta if llm_meta and llm_meta.startswith("router") else None,
    )
    _persist_verdict(verdict)

    # User-visible log line — terse but readable while tailing.
    summary = (
        f"watchdog: health={verdict.health} "
        f"bot={snap.bot_state} "
        f"events={snap.n_journal_events} "
        f"submitted={snap.submitted_count}/refused={snap.refusal_count} "
        f"agents_eval={sum(snap.eval_count_per_agent.values())} "
        f"actions={len(executed)} advisory={len(advisory)}"
    )
    if verdict.health == "critical":
        log.error(summary)
    elif verdict.health == "degraded":
        log.warning(summary)
    else:
        log.info(summary)
    if executed:
        for e in executed:
            log.info("watchdog: executed action %s", e)
    if advisory:
        for a in advisory:
            log.info("watchdog: advisory %s", a)

    return verdict


def _maybe_load_dotenv() -> None:
    """Best-effort .env loader so the LLM router sees keys.

    The runner already loads .env via uvicorn's startup; this script runs
    standalone so we replicate the load here. Silent if dotenv is missing
    (we'll fall back to whatever's in os.environ).
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LLM-driven bot watchdog agent.")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=300,
        help="Seconds between watchdog cycles (default: 300 = 5 min).",
    )
    parser.add_argument(
        "--api-base",
        type=str,
        default=_DEFAULT_API_BASE,
        help="Dashboard API base URL (default: http://localhost:8000).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit (used by tests).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    _maybe_load_dotenv()

    if args.once:
        run_cycle(api_base=args.api_base)
        return 0

    log.info(
        "watchdog_agent started (interval=%ds, api_base=%s)",
        args.interval_seconds,
        args.api_base,
    )
    while True:
        try:
            run_cycle(api_base=args.api_base)
        except Exception:  # never let the watchdog die silently
            log.exception("watchdog cycle crashed; sleeping then retrying")
        time.sleep(args.interval_seconds)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
