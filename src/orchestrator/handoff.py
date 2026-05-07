"""Atomic checkpoint primitives for multi-Claude session handoffs.

Brief path: live/orchestrator/handoff/<role>/brief.md
Writes are atomic via write-temp-rename so readers never see a partial file.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.config import PROJECT_ROOT

HANDOFF_DIR = PROJECT_ROOT / "live" / "orchestrator" / "handoff"


def _brief_path(role: str) -> Path:
    role_dir = HANDOFF_DIR / role
    role_dir.mkdir(parents=True, exist_ok=True)
    return role_dir / "brief.md"


def read_brief(role: str) -> str | None:
    """Return the current handoff brief for *role*, or None if absent."""
    path = _brief_path(role)
    if not path.exists():
        return None
    try:
        return path.read_text()
    except OSError:
        return None


def write_brief(role: str, body: str) -> None:
    """Write *body* as the handoff brief for *role* atomically."""
    path = _brief_path(role)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(body)
    os.rename(tmp, path)
