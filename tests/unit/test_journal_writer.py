"""JournalWriter: append, redaction, file naming."""

from __future__ import annotations

import json
from pathlib import Path

from src.journal.writer import JournalWriter, redact


def test_redact_inline_secret():
    out = redact({"msg": "API_KEY=ABC123XYZ_super_secret_value", "level": "info"})
    assert "REDACTED" in out["msg"]
    assert "ABC123XYZ_super_secret_value" not in out["msg"]
    assert out["level"] == "info"


def test_redact_long_token_under_secret_key():
    out = redact({"alpaca_secret": "x" * 40})
    assert out["alpaca_secret"] == "***REDACTED***"


def test_redact_short_value_under_secret_key_kept():
    out = redact({"secret_label": "personal"})
    assert out["secret_label"] == "personal"


def test_redact_recurses_into_dicts():
    out = redact({"data": {"api_key": "X" * 40, "user": "alice"}})
    assert out["data"]["api_key"] == "***REDACTED***"
    assert out["data"]["user"] == "alice"


def test_redact_recurses_into_lists():
    out = redact({"data": [{"token": "T" * 40}, {"name": "n"}]})
    assert out["data"][0]["token"] == "***REDACTED***"
    assert out["data"][1]["name"] == "n"


def test_writer_appends_one_jsonl_line(tmp_journal_dir: Path):
    w = JournalWriter(tmp_journal_dir)
    p1 = w.write({"event": "research", "subject": "SPY"})
    p2 = w.write({"event": "signal", "subject": "SPY"})
    assert p1 == p2
    lines = p1.read_text().strip().split("\n")
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    assert e1["event"] == "research"
    assert "ts" in e1


def test_writer_redacts_secrets_before_write(tmp_journal_dir: Path):
    w = JournalWriter(tmp_journal_dir)
    p = w.write({"event": "submit", "api_key": "X" * 40, "subject": "SPY"})
    body = p.read_text()
    assert "X" * 40 not in body
    assert "REDACTED" in body


def test_writer_creates_dir_if_missing(tmp_path):
    target = tmp_path / "newjournal"
    assert not target.exists()
    w = JournalWriter(target)
    p = w.write({"event": "x"})
    assert p.parent == target
    assert p.exists()
