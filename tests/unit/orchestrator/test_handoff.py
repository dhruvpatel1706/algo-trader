"""Unit tests for src.orchestrator.handoff — atomic brief primitives."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path: Path):
    from src.orchestrator import handoff as m

    monkeypatch.setattr(m, "HANDOFF_DIR", tmp_path / "handoff")


def test_read_brief_none_when_missing():
    from src.orchestrator.handoff import read_brief

    assert read_brief("watcher") is None


def test_write_and_read_roundtrip():
    from src.orchestrator.handoff import read_brief, write_brief

    write_brief("watcher", "# Watcher\nAll clear.")
    assert read_brief("watcher") == "# Watcher\nAll clear."


def test_write_overwrites_previous():
    from src.orchestrator.handoff import read_brief, write_brief

    write_brief("researcher", "v1")
    write_brief("researcher", "v2")
    assert read_brief("researcher") == "v2"


def test_write_creates_parent_dirs(tmp_path: Path):
    from src.orchestrator.handoff import HANDOFF_DIR, write_brief

    write_brief("new_role", "body text")
    assert (HANDOFF_DIR / "new_role" / "brief.md").exists()


def test_roles_are_independent():
    from src.orchestrator.handoff import read_brief, write_brief

    write_brief("watcher", "watcher body")
    write_brief("researcher", "researcher body")
    assert read_brief("watcher") == "watcher body"
    assert read_brief("researcher") == "researcher body"


def test_write_brief_empty_string():
    from src.orchestrator.handoff import read_brief, write_brief

    write_brief("operator", "")
    assert read_brief("operator") == ""
