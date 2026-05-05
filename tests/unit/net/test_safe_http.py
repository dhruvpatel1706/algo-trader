"""Tests for src.net.safe_http."""

from __future__ import annotations

import urllib.request
from unittest.mock import patch

import pytest
from src.net import UnsafeUrlError, safe_urlopen


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/data",
        "http://example.com/api",
        "javascript:alert(1)",
        "gopher://example.com",
    ],
)
def test_safe_urlopen_rejects_non_https(url):
    with pytest.raises(UnsafeUrlError):
        safe_urlopen(url, timeout=1)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_safe_urlopen_allows_http_localhost(host):
    """Local stub servers in tests use http://localhost — keep that working."""
    url = f"http://{host}:9999/probe"
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b"ok"
        safe_urlopen(url, timeout=1)
    mock_open.assert_called_once()


def test_safe_urlopen_allows_https():
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b"{}"
        safe_urlopen("https://api.example.com/v1/data", timeout=1)
    mock_open.assert_called_once()


def test_safe_urlopen_validates_request_object_url():
    """Request objects must also have their URL inspected."""
    req = urllib.request.Request("ftp://example.com/data")  # noqa: S310 — intentionally bad URL; the safe_urlopen call below MUST refuse it
    with pytest.raises(UnsafeUrlError):
        safe_urlopen(req, timeout=1)


def test_safe_urlopen_rejects_unknown_target_type():
    with pytest.raises(TypeError):
        safe_urlopen(123, timeout=1)  # type: ignore[arg-type]


def test_safe_urlopen_passes_timeout_through():
    with patch("urllib.request.urlopen") as mock_open:
        safe_urlopen("https://example.com", timeout=42)
    _args, kwargs = mock_open.call_args
    assert kwargs["timeout"] == 42


def test_safe_urlopen_allows_no_timeout():
    with patch("urllib.request.urlopen") as mock_open:
        safe_urlopen("https://example.com")
    _args, kwargs = mock_open.call_args
    assert "timeout" not in kwargs
