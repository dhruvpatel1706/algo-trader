"""Python facade for the Rust signal-engine extension.

If the native extension was built (via `maturin develop`), the four functions
below dispatch to Rust. If not, callers should import from `src.signals.indicators`
directly — that's the pure-Python fallback and the system of record.

Use case for the Rust path: tick-level backtests over multi-year datasets, where
indicator computation dominates the loop. For daily-bar production trading, the
broker round-trip dominates by ~20x and Rust is not worth the build complexity.
"""

from __future__ import annotations

try:
    from ._signal_engine import (  # type: ignore[attr-defined]
        atr,
        ema,
        sma,
        williams_vix_fix,
    )

    HAVE_NATIVE = True
except ImportError as e:
    HAVE_NATIVE = False
    _IMPORT_ERROR = e

    def _missing(*_args, **_kwargs):
        raise RuntimeError(
            "signal_engine_native is not built. Run "
            "`cd crates/signal-engine && maturin develop --release` "
            "or import the pure-Python equivalents from src.signals.indicators."
        ) from _IMPORT_ERROR

    sma = ema = atr = williams_vix_fix = _missing  # type: ignore[assignment]

__all__ = ["sma", "ema", "atr", "williams_vix_fix", "HAVE_NATIVE"]
