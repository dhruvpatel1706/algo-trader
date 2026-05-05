"""Tests for GovernanceMemory + the GovernanceAgent memory wiring."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from src.agents.base import AssetClass
from src.agents.governance_agent import GovernanceAgent
from src.agents.governance_memory import GovernanceMemory, MemoryEntry

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _status(
    name: str = "mr_etf",
    state: str = "live",
    coherence: float = 0.3,
):
    return SimpleNamespace(
        name=name,
        asset_class=AssetClass.EQUITY,
        state=state,
        heat_allocation=0.1,
        coherence=coherence,
        n_open_positions=0,
        last_eval_ts=datetime.now(UTC),
        notes="",
    )


# ---------------------------------------------------------------------------
# GovernanceMemory unit tests
# ---------------------------------------------------------------------------


def test_add_then_recent_round_trips_an_entry(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    rid = mem.add(
        kind="observation",
        content="coherence dropped to 0.31",
        target_strategy="mr_etf",
        metadata={"coherence": 0.31, "max_dd": 0.07},
    )
    assert rid >= 1
    rows = mem.recent()
    assert len(rows) == 1
    e = rows[0]
    assert isinstance(e, MemoryEntry)
    assert e.id == rid
    assert e.kind == "observation"
    assert e.target_strategy == "mr_etf"
    assert e.content == "coherence dropped to 0.31"
    assert e.metadata == {"coherence": 0.31, "max_dd": 0.07}
    assert e.ts.tzinfo is not None
    mem.close()


def test_recent_filters_by_kind(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    mem.add(kind="observation", content="obs-1")
    mem.add(kind="decision", content="dec-1", target_strategy="mr_etf")
    mem.add(kind="halt", content="halt-1", target_strategy="mr_etf")

    decisions = mem.recent(kinds=["decision"])
    assert len(decisions) == 1
    assert decisions[0].kind == "decision"
    assert decisions[0].content == "dec-1"

    multi = mem.recent(kinds=["decision", "halt"])
    assert {e.kind for e in multi} == {"decision", "halt"}

    # Empty kinds list -> nothing
    assert mem.recent(kinds=[]) == []
    mem.close()


def test_recent_filters_by_target_strategy(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    mem.add(kind="decision", content="kill mr_etf", target_strategy="mr_etf")
    mem.add(kind="decision", content="kill xyz", target_strategy="xyz")
    mem.add(kind="observation", content="portfolio-wide note")  # target=None

    only_mr = mem.recent(target_strategy="mr_etf")
    assert len(only_mr) == 1
    assert only_mr[0].target_strategy == "mr_etf"

    # Combined filters
    combo = mem.recent(kinds=["decision"], target_strategy="xyz")
    assert len(combo) == 1
    assert combo[0].content == "kill xyz"
    mem.close()


def test_recent_orders_newest_first_and_respects_limit(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    for i in range(5):
        mem.add(kind="observation", content=f"obs-{i}")
    rows = mem.recent(limit=3)
    assert len(rows) == 3
    # autoincrement id is monotonic; newest first means descending id.
    ids = [r.id for r in rows]
    assert ids == sorted(ids, reverse=True)
    assert rows[0].content == "obs-4"
    mem.close()


def test_summary_for_prompt_is_non_empty_with_recent_entries(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    mem.add(kind="decision", content="killed mr_etf", target_strategy="mr_etf")
    mem.add(kind="observation", content="heat at 0.04")
    summary = mem.summary_for_prompt()
    assert summary  # non-empty
    assert "killed mr_etf" in summary
    assert "DECISION" in summary
    assert "OBSERVATION" in summary
    mem.close()


def test_summary_for_prompt_respects_max_chars(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    for i in range(50):
        mem.add(kind="observation", content=f"line-{i}-" + ("x" * 50))
    summary = mem.summary_for_prompt(max_chars=200)
    assert len(summary) <= 200
    # Should still contain some content even with tight budget
    assert summary  # non-empty
    mem.close()


def test_summary_for_prompt_empty_when_no_entries(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    assert mem.summary_for_prompt() == ""
    mem.close()


def test_compact_keep_days_zero_removes_everything(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    for i in range(5):
        mem.add(kind="observation", content=f"obs-{i}")
    removed = mem.compact(keep_days=0)
    assert removed == 5
    assert mem.recent() == []
    mem.close()


def test_compact_returns_zero_when_nothing_old(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    mem.add(kind="observation", content="fresh")
    removed = mem.compact(keep_days=365)
    assert removed == 0
    assert len(mem.recent()) == 1
    mem.close()


def test_long_content_gets_truncated_with_ellipsis_suffix(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    big = "z" * 5000
    mem.add(kind="observation", content=big)
    rows = mem.recent()
    assert len(rows) == 1
    stored = rows[0].content
    assert len(stored) == 4000
    assert stored.endswith("...")
    # The non-ellipsis prefix must come from the original content.
    assert stored[: 4000 - 3] == "z" * (4000 - 3)
    mem.close()


def test_short_content_is_not_truncated(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    msg = "hello world"
    mem.add(kind="observation", content=msg)
    assert mem.recent()[0].content == msg
    mem.close()


def test_wal_journal_mode_is_set(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    mode = mem._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    mem.close()


def test_concurrent_instances_see_each_others_writes(tmp_path):
    db_path = tmp_path / "mem.db"
    a = GovernanceMemory(db_path=db_path)
    b = GovernanceMemory(db_path=db_path)
    a.add(kind="decision", content="from-a", target_strategy="mr_etf")
    # b must see a's write without explicit refresh — autocommit + WAL.
    rows_b = b.recent()
    assert len(rows_b) == 1
    assert rows_b[0].content == "from-a"

    b.add(kind="observation", content="from-b")
    rows_a = a.recent()
    assert len(rows_a) == 2
    assert {r.content for r in rows_a} == {"from-a", "from-b"}
    a.close()
    b.close()


def test_memory_file_is_created_if_missing(tmp_path):
    nested = tmp_path / "nested" / "dir" / "mem.db"
    assert not nested.exists()
    mem = GovernanceMemory(db_path=nested)
    assert nested.exists()
    mem.add(kind="observation", content="x")
    mem.close()


def test_default_db_path_uses_live_dir(tmp_path, monkeypatch):
    # Redirect the module-level PROJECT_ROOT so we don't write to the real
    # repo's live/ directory in tests.
    import src.agents.governance_memory as gm

    monkeypatch.setattr(gm, "PROJECT_ROOT", tmp_path)
    mem = gm.GovernanceMemory()
    expected = tmp_path / "live" / "governance_memory.db"
    assert mem._path == expected
    assert expected.parent.exists()
    mem.close()


def test_invalid_kind_raises(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    import pytest

    with pytest.raises(ValueError):
        mem.add(kind="bogus", content="x")  # type: ignore[arg-type]
    mem.close()


def test_metadata_round_trips_complex_values(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    payload = {
        "action": "kill",
        "confidence": 0.7,
        "tags": ["drift", "low-coherence"],
        "nested": {"win_rate": 0.42},
    }
    mem.add(kind="decision", content="x", metadata=payload)
    e = mem.recent()[0]
    assert e.metadata == payload
    mem.close()


def test_close_is_idempotent(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    mem.close()
    mem.close()  # must not raise


# ---------------------------------------------------------------------------
# GovernanceAgent + memory wiring
# ---------------------------------------------------------------------------


def test_agent_without_memory_evaluates_without_persistence(tmp_path):
    agent = GovernanceAgent(coherence_kill_threshold=0.5)
    recs = agent.evaluate([_status(name="mr_etf", state="live", coherence=0.2)])
    assert len(recs) == 1
    assert agent.memory_summary() == ""


def test_agent_persists_each_decision_to_memory(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    agent = GovernanceAgent(coherence_kill_threshold=0.5, memory=mem)
    state = [
        _status(name="mr_etf", state="live", coherence=0.2),
        _status(name="ma_pullback_trend", state="halted"),
        # Healthy coherence — produces no decision and therefore no memory write.
        _status(name="wheel_etf", state="live", coherence=0.95),
    ]
    recs = agent.evaluate(state)
    assert len(recs) == 2
    rows = mem.recent(kinds=["decision"])
    assert len(rows) == 2
    targets = {r.target_strategy for r in rows}
    assert targets == {"mr_etf", "ma_pullback_trend"}
    actions = {r.metadata.get("action") for r in rows}
    assert actions == {"kill", "investigate"}
    mem.close()


def test_agent_memory_summary_returns_recent_entries(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    agent = GovernanceAgent(coherence_kill_threshold=0.5, memory=mem)
    agent.evaluate([_status(name="mr_etf", state="live", coherence=0.2)])
    summary = agent.memory_summary()
    assert summary
    assert "mr_etf" in summary
    assert "coherence" in summary.lower()
    mem.close()


def test_agent_evaluate_with_no_state_does_not_write(tmp_path):
    mem = GovernanceMemory(db_path=tmp_path / "mem.db")
    agent = GovernanceAgent(memory=mem)
    agent.evaluate(None)
    agent.evaluate({"SPY": object()})
    assert mem.recent() == []
    mem.close()


def test_agent_signature_unchanged_for_existing_callers(tmp_path):
    """The Agent ABC contract must still work without the new memory kwarg."""
    agent = GovernanceAgent()
    assert agent.evaluate(None) == []
    assert agent.evaluate({"SPY": object()}) == []
    # Memory not attached -> summary is empty.
    assert agent.memory_summary() == ""
