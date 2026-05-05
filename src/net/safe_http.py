"""HTTPS-only urlopen wrapper.

Plain `urllib.request.urlopen` accepts any scheme — `file://`, `ftp://`, even
custom protocol handlers. When a URL or `Request` object is built from data we
don't fully trust (config files, API responses, environment variables), bandit
flags this as an audit point (B310) because a malicious or misconfigured input
can be redirected to read local files or hit unexpected services.

This wrapper:

1. Resolves the URL string, whether the caller passed a raw `str` or a
   `urllib.request.Request`.
2. Rejects anything whose scheme is not `https`. The one allowed exception is
   `http://localhost`/`http://127.0.0.1` — useful for tests against a stub
   server. Plain `http://` to anything else is rejected.
3. Delegates to `urllib.request.urlopen` once the URL has been validated.

The callers in `src/data/*`, `src/research/*`, and `src/observability/*` use
this exclusively, so a future regression where a `_URL = "http://..."` constant
slips into a hot path is caught at the call site rather than reaching the
network with the wrong scheme.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any
from urllib.request import Request

_LOCALHOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class UnsafeUrlError(ValueError):
    """Raised when a URL fails the https-only guard."""


def _extract_url(target: str | Request) -> str:
    if isinstance(target, str):
        return target
    if isinstance(target, Request):
        return target.full_url
    raise TypeError(f"safe_urlopen expects str or urllib.request.Request, got {type(target)!r}")


def _is_safe(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and parsed.hostname in _LOCALHOSTS:
        return True
    return False


def safe_urlopen(target: str | Request, *, timeout: float | None = None, **kwargs: Any) -> Any:
    """Open `target` only if it uses https (or http+localhost).

    Mirrors `urllib.request.urlopen`'s signature for `timeout` and any extra
    kwargs are forwarded. Raises `UnsafeUrlError` for any scheme other than
    `https` (with a localhost-only exception for `http`).
    """
    url = _extract_url(target)
    if not _is_safe(url):
        raise UnsafeUrlError(f"refusing non-https URL: {url!r}")
    if timeout is None:
        return urllib.request.urlopen(target, **kwargs)  # noqa: S310  # nosec B310 — _is_safe guard above
    return urllib.request.urlopen(target, timeout=timeout, **kwargs)  # noqa: S310  # nosec B310 — _is_safe guard above


__all__ = ["UnsafeUrlError", "safe_urlopen"]
