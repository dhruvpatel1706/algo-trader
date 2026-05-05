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


def test_redact_account_id_under_account_key():
    """Broker account identifiers are sensitive — strip when keyed as such."""
    out = redact({"account_id": "PA12ABC34DEF56GHI78JKL90M"})
    assert out["account_id"] == "***REDACTED***"


def test_redact_account_number_alias():
    out = redact({"account_number": "PA1234567890ABCDEFGHIJKLM"})
    assert out["account_number"] == "***REDACTED***"


def test_redact_webhook_url_value():
    """Discord webhook URLs are themselves secrets — anyone with the URL can post."""
    webhook = "https://discord.com/api/webhooks/123456/abcDEFghi-_LongTokenValueHere"
    out = redact({"webhook_url": webhook})
    assert out["webhook_url"] == "***REDACTED***"


def test_redact_webhook_in_message_body():
    """Even if webhook URL appears inside an unrelated string field, scrub it."""
    msg = "Sent alert via https://discord.com/api/webhooks/999/xyzTOKEN at 12:34"
    out = redact({"event": "alert", "message": msg})
    assert "discord.com/api/webhooks" not in out["message"]
    assert "REDACTED_WEBHOOK" in out["message"]


def test_redact_short_value_under_secret_label_still_kept():
    """Don't over-redact: a short non-credential string is fine."""
    out = redact({"secret_label": "personal"})
    assert out["secret_label"] == "personal"
