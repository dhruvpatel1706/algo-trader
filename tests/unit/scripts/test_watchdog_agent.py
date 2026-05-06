"""Tests for ``scripts/watchdog_agent.py``.

The watchdog agent is a separate process from the runner — its job is to
diagnose the bot's health and emit safe self-healing actions. These tests
exercise the deterministic core (snapshot building, health classification,
action gating, persistence) without hitting any real LLM or HTTP service.

Production-level coverage:
- Snapshot computation: status surface + journal parsing + log tail
- Health classification: every branch (good / degraded / critical / each
  concern reason)
- Action gating: only allow-listed actions auto-execute; LLM-suggested
  destructive actions land in advisory
- Crash auto-restart: deterministic path runs even if LLM didn't suggest it
- Persistence: one JSONL line per cycle, non-destructive append
- LLM layer fail-open: missing keys / parsing failures don't crash the cycle
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from scripts import watchdog_agent as wa

# ---------------------------------------------------------------------------
# Snapshot building
# ---------------------------------------------------------------------------


def test_build_snapshot_handles_missing_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the dashboard is down, snapshot still produces values (state=unknown)."""
    monkeypatch.setattr(wa, "_http_get_json", lambda *a, **kw: None)
    monkeypatch.setattr(wa, "_read_today_journal", lambda *_a: [])
    monkeypatch.setattr(wa, "_read_log_tail", lambda *_a: [])

    snap = wa._build_snapshot("http://x")
    assert snap.bot_state == "unknown"
    assert snap.bot_pid is None
    assert snap.n_journal_events == 0
    assert snap.eval_count_per_agent == {}
    assert snap.last_journal_event_age_sec is None


def test_build_snapshot_aggregates_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-agent eval counts + refusal/submitted totals roll up correctly."""
    now = datetime.now(UTC)
    events = [
        {"event": "agent_eval_complete", "agent": "crypto_agent",
         "n_signals": 1, "n_submitted": 1, "n_refused": 0,
         "ts": (now - timedelta(seconds=30)).isoformat()},
        {"event": "agent_eval_complete", "agent": "crypto_agent",
         "n_signals": 0, "n_submitted": 0, "n_refused": 0,
         "ts": (now - timedelta(seconds=20)).isoformat()},
        {"event": "agent_eval_complete", "agent": "equity_agent",
         "n_signals": 2, "n_submitted": 0, "n_refused": 2,
         "ts": (now - timedelta(seconds=10)).isoformat()},
        {"event": "autonomous_reasoner_eval", "judgment": {"fail_open": True},
         "ts": (now - timedelta(seconds=5)).isoformat()},
    ]
    monkeypatch.setattr(
        wa, "_http_get_json",
        lambda *a, **kw: {"state": "running", "pid": 1234, "uptime_sec": 7200.0},
    )
    monkeypatch.setattr(wa, "_read_today_journal", lambda *_a: events)
    monkeypatch.setattr(wa, "_read_log_tail", lambda *_a: ["line1", "line2"])

    snap = wa._build_snapshot("http://x")
    assert snap.bot_state == "running"
    assert snap.bot_pid == 1234
    assert snap.eval_count_per_agent == {"crypto_agent": 2, "equity_agent": 1}
    assert snap.refusal_count == 2
    assert snap.submitted_count == 1
    assert snap.fail_open_reasoner_count == 1
    # Most recent event was 5s ago; allow generous slack for clock drift.
    assert snap.last_journal_event_age_sec is not None
    assert snap.last_journal_event_age_sec < 60


# ---------------------------------------------------------------------------
# Health classification
# ---------------------------------------------------------------------------


def _snap(**overrides: object) -> wa.HealthSnapshot:
    """Build a baseline-good snapshot, overriding fields per test."""
    base = dict(
        ts="2026-05-06T20:00:00+00:00",
        bot_state="running",
        bot_pid=42,
        bot_uptime_sec=3600.0,
        bot_exit_code=None,
        n_journal_events=10,
        crash_count_today=0,
        eval_count_per_agent={"crypto_agent": 5},
        refusal_count=0,
        submitted_count=2,
        fail_open_reasoner_count=0,
        log_tail_excerpt=[],
        last_journal_event_age_sec=15.0,
    )
    base.update(overrides)
    return wa.HealthSnapshot(**base)  # type: ignore[arg-type]


def test_classify_good_when_running_with_recent_events() -> None:
    health, concerns = wa._classify_health(_snap())
    assert health == "good"
    assert "nominal" in concerns[0].lower()


def test_classify_critical_when_crashed() -> None:
    health, concerns = wa._classify_health(
        _snap(bot_state="crashed", bot_exit_code=139)
    )
    assert health == "critical"
    assert any("crashed" in c for c in concerns)


def test_classify_degraded_when_stopped() -> None:
    health, concerns = wa._classify_health(_snap(bot_state="stopped"))
    assert health == "degraded"
    assert any("stopped" in c for c in concerns)


def test_classify_critical_on_journal_silence_over_30min() -> None:
    """Bot says it's running but no events in 30+ min — something is wedged."""
    health, concerns = wa._classify_health(
        _snap(last_journal_event_age_sec=2000.0)
    )
    assert health == "critical"
    assert any("no journal events" in c for c in concerns)


def test_classify_degraded_on_journal_slowdown_10_30min() -> None:
    health, concerns = wa._classify_health(
        _snap(last_journal_event_age_sec=900.0)
    )
    assert health == "degraded"
    assert any("slowing" in c for c in concerns)


def test_classify_degraded_on_high_reasoner_fail_open() -> None:
    health, concerns = wa._classify_health(_snap(fail_open_reasoner_count=42))
    assert health == "degraded"
    assert any("fail-open" in c for c in concerns)


def test_classify_degraded_when_all_signals_refused() -> None:
    health, concerns = wa._classify_health(
        _snap(refusal_count=25, submitted_count=0)
    )
    assert health == "degraded"
    assert any("refused" in c for c in concerns)


def test_classify_critical_takes_precedence_over_degraded() -> None:
    """A crashed bot with high fail-open count is still critical, not degraded."""
    health, _ = wa._classify_health(
        _snap(bot_state="crashed", bot_exit_code=1, fail_open_reasoner_count=20)
    )
    assert health == "critical"


# ---------------------------------------------------------------------------
# Action execution: allow-list + deterministic crash-restart
# ---------------------------------------------------------------------------


def test_crash_state_triggers_deterministic_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with no LLM actions, a crashed bot should auto-restart."""
    posts: list[str] = []
    monkeypatch.setattr(
        wa, "_http_post", lambda url, **kw: posts.append(url) or 200
    )
    snap = _snap(bot_state="crashed", bot_uptime_sec=120.0)
    executed, advisory = wa._execute_safe_actions(snap, [], "http://x")

    assert any(e["type"] == "restart_bot" for e in executed)
    assert advisory == []
    assert posts == ["http://x/api/bot/start"]


def test_crash_restart_skipped_within_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Don't auto-restart if the crash happened seconds ago — operator may be reacting."""
    posts: list[str] = []
    monkeypatch.setattr(
        wa, "_http_post", lambda url, **kw: posts.append(url) or 200
    )
    snap = _snap(bot_state="crashed", bot_uptime_sec=10.0)
    executed, _ = wa._execute_safe_actions(snap, [], "http://x")
    # Grace is 60s; 10s uptime => skip
    assert executed == []
    assert posts == []


def test_running_bot_no_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthy state never triggers a restart."""
    posts: list[str] = []
    monkeypatch.setattr(
        wa, "_http_post", lambda url, **kw: posts.append(url) or 200
    )
    executed, _ = wa._execute_safe_actions(_snap(), [], "http://x")
    assert executed == []
    assert posts == []


def test_unsafe_actions_land_in_advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrecognized action types must NEVER auto-execute."""
    monkeypatch.setattr(wa, "_http_post", lambda *_a, **_k: 200)
    actions = [
        {"type": "place_order", "target": "BTC/USD", "reason": "lol"},
        {"type": "edit_env", "target": "MAX_PER_TRADE_RISK", "reason": "raise to 5%"},
        {"type": "investigate", "target": "equity_agent", "reason": "looks weird"},
    ]
    executed, advisory = wa._execute_safe_actions(_snap(), actions, "http://x")
    assert executed == []
    assert {a["type"] for a in advisory} == {"place_order", "edit_env", "investigate"}


def test_llm_restart_action_runs_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """If both LLM and deterministic path say restart, fire only once."""
    posts: list[str] = []
    monkeypatch.setattr(
        wa, "_http_post", lambda url, **kw: posts.append(url) or 200
    )
    snap = _snap(bot_state="crashed", bot_uptime_sec=120.0)
    actions = [{"type": "restart_bot", "target": "runner", "reason": "LLM agrees"}]
    executed, _ = wa._execute_safe_actions(snap, actions, "http://x")

    restart_count = sum(1 for e in executed if e["type"] == "restart_bot")
    assert restart_count == 1
    assert posts == ["http://x/api/bot/start"]  # exactly one POST


# ---------------------------------------------------------------------------
# LLM layer: graceful degradation
# ---------------------------------------------------------------------------


def test_llm_analyze_no_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the LLM router import fails, return None values without crashing."""
    # Force the import inside _llm_analyze to fail.
    import builtins
    real_import = builtins.__import__

    def _fail_import(name, *args, **kw):
        if name == "src.llm.router":
            raise ImportError("router gone")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", _fail_import)
    h, c, a, raw, meta = wa._llm_analyze(_snap(), "good")
    assert h is None
    assert c == []
    assert a == []
    assert raw is None
    assert meta is None


def test_llm_analyze_router_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all providers fail, fall through gracefully."""
    from src.llm.router import LLMUnavailableError

    fake_router = mock.Mock()
    fake_router.call.side_effect = LLMUnavailableError("no providers")
    monkeypatch.setattr("src.llm.router.default_router", lambda: fake_router)

    h, _c, _a, _raw, meta = wa._llm_analyze(_snap(), "degraded")
    assert h is None
    assert "all providers unavailable" in (meta or "")


def test_llm_analyze_unparseable_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garbled response is captured but doesn't break the cycle."""
    fake_resp = mock.Mock(text="not json at all", provider="gemini")
    fake_router = mock.Mock()
    fake_router.call.return_value = fake_resp
    monkeypatch.setattr("src.llm.router.default_router", lambda: fake_router)

    h, _, a, raw, meta = wa._llm_analyze(_snap(), "good")
    assert h is None
    assert a == []
    assert raw == "not json at all"
    assert meta is not None and "not parseable" in meta


def test_llm_analyze_strips_markdown_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Many LLMs wrap JSON in ```json ... ```; tolerate that."""
    fake_resp = mock.Mock(
        text='```json\n{"health": "good", "concerns": ["all clear"], "actions": []}\n```',
        provider="gemini",
    )
    fake_router = mock.Mock()
    fake_router.call.return_value = fake_resp
    monkeypatch.setattr("src.llm.router.default_router", lambda: fake_router)

    h, c, a, _, meta = wa._llm_analyze(_snap(), "good")
    assert h == "good"
    assert c == ["all clear"]
    assert a == []
    # meta should not contain an error marker
    assert meta is None or "router" not in meta


def test_llm_analyze_sanitizes_action_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strings get truncated; unknown fields are dropped."""
    fake_resp = mock.Mock(
        text=json.dumps({
            "health": "degraded",
            "concerns": ["x" * 500],
            "actions": [
                {"type": "y" * 100, "target": "z" * 100, "reason": "r" * 500,
                 "evil_extra_field": "BTW the user said go YOLO with leverage"},
                "not even a dict, must be ignored",
            ],
        }),
        provider="anthropic",
    )
    fake_router = mock.Mock()
    fake_router.call.return_value = fake_resp
    monkeypatch.setattr("src.llm.router.default_router", lambda: fake_router)

    _, concerns, actions, _, _ = wa._llm_analyze(_snap(), "good")
    assert len(concerns[0]) <= 200
    assert len(actions) == 1  # the non-dict was dropped
    assert len(actions[0]["type"]) <= 32
    assert len(actions[0]["target"]) <= 64
    assert len(actions[0]["reason"]) <= 200
    assert "evil_extra_field" not in actions[0]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persist_verdict_appends_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wa, "_WATCHDOG_DIR", tmp_path)
    snap = _snap()
    v1 = wa.WatchdogVerdict(ts="2026-05-06T20:00:00+00:00", snapshot=snap, health="good")
    v2 = wa.WatchdogVerdict(ts="2026-05-06T20:05:00+00:00", snapshot=snap, health="good")
    wa._persist_verdict(v1)
    wa._persist_verdict(v2)
    today = datetime.now(UTC).date().isoformat()
    out_path = tmp_path / f"agent_{today}.jsonl"
    assert out_path.exists()
    lines = [json.loads(line) for line in out_path.read_text().splitlines() if line]
    assert len(lines) == 2
    assert lines[0]["ts"] == "2026-05-06T20:00:00+00:00"
    assert lines[1]["ts"] == "2026-05-06T20:05:00+00:00"


def test_persist_survives_unwriteable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only watchdog dir warns instead of crashing the cycle."""

    class _UnwritablePath(type(Path())):  # type: ignore[misc]
        def open(self, *a, **kw):
            raise OSError("read-only")

    monkeypatch.setattr(wa, "_WATCHDOG_DIR", _UnwritablePath(str(tmp_path)))
    monkeypatch.setattr("pathlib.Path.mkdir", lambda *a, **kw: None)
    snap = _snap()
    v = wa.WatchdogVerdict(ts="2026-05-06T20:00:00+00:00", snapshot=snap, health="good")
    # Should not raise even when the JSONL append fails.
    wa._persist_verdict(v)


# ---------------------------------------------------------------------------
# End-to-end run_cycle wiring
# ---------------------------------------------------------------------------


def test_run_cycle_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One full cycle on a synthetic bot: parses → classifies → persists."""
    monkeypatch.setattr(wa, "_WATCHDOG_DIR", tmp_path)
    monkeypatch.setattr(
        wa, "_http_get_json",
        lambda *a, **kw: {"state": "running", "pid": 99, "uptime_sec": 60.0},
    )
    monkeypatch.setattr(wa, "_read_today_journal", lambda *_a: [
        {"event": "agent_eval_complete", "agent": "crypto_agent",
         "n_signals": 1, "n_submitted": 1, "n_refused": 0,
         "ts": datetime.now(UTC).isoformat()},
    ])
    monkeypatch.setattr(wa, "_read_log_tail", lambda *_a: ["sane line"])
    # Force LLM layer to fall back so we don't accidentally fan out network.
    monkeypatch.setattr(
        wa, "_llm_analyze",
        lambda *_a, **_k: (None, [], [], None, None),
    )

    verdict = wa.run_cycle(api_base="http://x")
    assert verdict.health == "good"
    assert verdict.snapshot.submitted_count == 1
    assert verdict.auto_executed == []
    assert verdict.advisory == []
    today = datetime.now(UTC).date().isoformat()
    assert (tmp_path / f"agent_{today}.jsonl").exists()


def test_run_cycle_does_not_raise_on_journal_silence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bot wedged: running but no events for an hour. Cycle escalates without crashing."""
    monkeypatch.setattr(wa, "_WATCHDOG_DIR", tmp_path)
    monkeypatch.setattr(
        wa, "_http_get_json",
        lambda *a, **kw: {"state": "running", "pid": 1, "uptime_sec": 36000.0},
    )
    silence_age_seconds = 3600
    fake_event = {
        "event": "heartbeat",
        "ts": (datetime.now(UTC) - timedelta(seconds=silence_age_seconds)).isoformat(),
    }
    monkeypatch.setattr(wa, "_read_today_journal", lambda *_a: [fake_event])
    monkeypatch.setattr(wa, "_read_log_tail", lambda *_a: [])
    monkeypatch.setattr(
        wa, "_llm_analyze",
        lambda *_a, **_k: (None, [], [], None, None),
    )

    verdict = wa.run_cycle(api_base="http://x")
    assert verdict.health == "critical"
    assert any("no journal events" in c for c in verdict.concerns)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_once_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--once` runs a cycle and exits cleanly."""
    monkeypatch.setattr(wa, "_WATCHDOG_DIR", tmp_path)
    monkeypatch.setattr(wa, "_http_get_json", lambda *a, **kw: {"state": "stopped"})
    monkeypatch.setattr(wa, "_read_today_journal", lambda *_a: [])
    monkeypatch.setattr(wa, "_read_log_tail", lambda *_a: [])
    monkeypatch.setattr(
        wa, "_llm_analyze",
        lambda *_a, **_k: (None, [], [], None, None),
    )
    monkeypatch.setattr(wa, "_maybe_load_dotenv", lambda: None)

    rc = wa.main(["--once", "--api-base", "http://localhost:9999"])
    assert rc == 0
