"""Append-only JSONL journal with redaction + fsync."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Inline secrets like `API_KEY=abc123` inside string values.
_INLINE_SECRET = re.compile(r"(?i)(api[_-]?key|secret(?:_key)?|token|bearer|password)\s*[:=]\s*\S+")
# Substrings of dict KEYS that imply the VALUE is a secret.
_SECRET_KEY_HINT = re.compile(r"(?i)(key|secret|token|password|bearer)")
# Long alphanumeric strings — probably credentials when stored under a sensitive key.
_LONG_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{32,}$")


def _redact_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        if _SECRET_KEY_HINT.search(key) and _LONG_TOKEN.match(value):
            return "***REDACTED***"
        return _INLINE_SECRET.sub(r"\1=***REDACTED***", value)
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, v) for v in value]
    return value


def redact(event: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted copy of `event`. Never mutates input."""
    return {k: _redact_value(k, v) for k, v in event.items()}


class JournalWriter:
    """Append one JSONL record per call. fsync after every write."""

    def __init__(self, journal_dir: Path) -> None:
        self._dir = Path(journal_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, dt: datetime | None = None) -> Path:
        d = dt or datetime.now(UTC)
        return self._dir / f"{d.strftime('%Y-%m-%d')}.jsonl"

    def write(self, event: dict[str, Any]) -> Path:
        if "ts" not in event:
            event = {**event, "ts": datetime.now(UTC).isoformat()}
        record = redact(event)
        path = self.path_for()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":"), default=str))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        return path
