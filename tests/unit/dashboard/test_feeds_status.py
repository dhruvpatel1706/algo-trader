"""Tests for ``dashboard/api/feeds_status.py``.

The endpoint introspects ``os.environ`` and reports which integration keys
are configured. Two cardinal rules:

1. NEVER returns key material — only ``configured: bool`` and a 4-char tail
   preview (and even the preview is suppressed for keys shorter than 8
   chars to avoid leaking short test fixtures).
2. Empty / whitespace-only env values count as NOT configured. A stale
   ``export FOO=`` line in the parent shell shouldn't show up as a
   green chip.
"""

from __future__ import annotations

import pytest
from dashboard.api.feeds_status import _FEEDS, _preview, _read_status


def test_preview_too_short_returns_none() -> None:
    """7-char string (or shorter) refuses to preview — too risky to leak."""
    assert _preview("") is None
    assert _preview("a") is None
    assert _preview("abcdefg") is None  # 7 chars


def test_preview_returns_last_four_with_ellipsis() -> None:
    """Long key returns last 4 chars prefixed with horizontal ellipsis."""
    assert _preview("sk-ant-api03-XXXXXXXXNTY7") == "…NTY7"
    assert _preview("01234567") == "…4567"  # exactly 8 chars


def test_preview_strips_whitespace() -> None:
    """A key with leading/trailing whitespace shouldn't end with ' \\n'."""
    assert _preview("  abcdefghij  ") == "…ghij"


def test_read_status_empty_env_reports_all_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env vars set → every feed is configured=False."""
    for spec in _FEEDS:
        monkeypatch.delenv(spec.env_var, raising=False)
    r = _read_status()
    assert r.n_total == len(_FEEDS)
    assert r.n_configured == 0
    assert all(f.configured is False for f in r.feeds)
    assert all(f.preview is None for f in r.feeds)


def test_read_status_whitespace_only_counts_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``export FOO=`` and ``export FOO='  '`` are NOT configured."""
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "   \t")
    monkeypatch.setenv("FINNHUB_API_KEY", "real-finnhub-key-12345")
    r = _read_status()
    by_var = {f.env_var: f for f in r.feeds}
    assert by_var["ALPACA_API_KEY"].configured is False
    assert by_var["GEMINI_API_KEY"].configured is False
    assert by_var["FINNHUB_API_KEY"].configured is True


def test_read_status_configured_feed_includes_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real-looking key shows a 4-char tail preview."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-XXXXXXXXXXXXNTY7")
    r = _read_status()
    by_var = {f.env_var: f for f in r.feeds}
    assert by_var["ANTHROPIC_API_KEY"].configured is True
    assert by_var["ANTHROPIC_API_KEY"].preview == "…NTY7"


def test_read_status_never_leaks_full_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: the full key string must never appear anywhere in the response."""
    secret = "sk-ant-VERY-SECRET-XXXXXXXXXXXX-NEVER-LEAK"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    r = _read_status()
    blob = r.model_dump_json()
    assert "VERY-SECRET" not in blob
    assert "NEVER-LEAK" not in blob
    # Tail preview is fine; the key body is not.
    assert "EAK" in blob  # last 3 chars in the tail; documenting expected exposure


def test_read_status_preserves_catalog_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feed order is stable so the UI chip strip doesn't reorder per refresh."""
    for spec in _FEEDS:
        monkeypatch.delenv(spec.env_var, raising=False)
    r = _read_status()
    returned_vars = [f.env_var for f in r.feeds]
    expected = [s.env_var for s in _FEEDS]
    assert returned_vars == expected


def test_read_status_n_configured_matches_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """``n_configured`` equals the number of ``configured=True`` feeds."""
    for spec in _FEEDS:
        monkeypatch.delenv(spec.env_var, raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "key1key1key1")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "key2key2key2")
    monkeypatch.setenv("GEMINI_API_KEY", "key3key3key3")
    r = _read_status()
    assert r.n_configured == 3
    actual = sum(1 for f in r.feeds if f.configured)
    assert actual == 3


def test_endpoint_route_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test the FastAPI route end-to-end."""
    from dashboard.api.feeds_status import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    monkeypatch.setenv("ALPACA_API_KEY", "test-key-XXXX")
    r = client.get("/api/feeds/status")
    assert r.status_code == 200
    body = r.json()
    assert "feeds" in body
    assert "n_configured" in body
    assert "n_total" in body
    assert body["n_total"] == len(_FEEDS)
