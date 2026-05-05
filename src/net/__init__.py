"""Network helpers."""

from src.net.safe_http import UnsafeUrlError, safe_urlopen

__all__ = ["UnsafeUrlError", "safe_urlopen"]
