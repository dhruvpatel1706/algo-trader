"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.config import get_settings


@pytest.fixture(autouse=True)
def _safe_paper_env(monkeypatch):
    """All tests run with paper-only env. Fresh settings per test."""
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "True")
    monkeypatch.setenv("ALPACA_API_KEY", "test_key_unused_in_unit_tests")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret_unused_in_unit_tests")
    monkeypatch.setenv("LIVE_TRADING", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def tmp_journal_dir(tmp_path: Path) -> Path:
    d = tmp_path / "journal"
    d.mkdir()
    return d
