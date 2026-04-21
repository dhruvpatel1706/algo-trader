"""Data-loader correctness tests.

The NVDA split test is a network-touching regression for the auto_adjust bug.
Marked `network`; skips cleanly if yfinance is unreachable or rate-limited.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.data.loader import load_daily_bars


@pytest.mark.network
def test_nvda_2024_06_split_is_adjusted_in_loader(monkeypatch):
    """NVDA had a 10:1 split effective 2024-06-10. With auto_adjust=True the
    historical close on 2024-06-07 is divided by 10 (split-adjusted backward),
    so the close ratio close[06-10] / close[06-07] should be ~1.0 (a normal
    daily move) instead of ~0.10 (which is what the unadjusted series prints).

    This is the regression test for the bug where auto_adjust=False let a
    fake -90% gap trigger ATR stops on every affected day.
    """
    # Force the loader to use yfinance directly. The autouse fixture sets dummy
    # Alpaca creds; with non-empty creds the loader hits Alpaca first and the
    # 401 is uncaught. Empty creds short-circuit straight to yfinance.
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "")
    from src.config import get_settings

    get_settings.cache_clear()

    bars = load_daily_bars(
        ("NVDA",),
        date(2024, 6, 1),
        date(2024, 6, 30),
        use_cache=False,
        fallback_to_yfinance=True,
    )
    if "NVDA" not in bars or bars["NVDA"].empty:
        pytest.skip("yfinance returned no data for NVDA (network or rate-limit)")

    df = bars["NVDA"].copy()
    # Index can be tz-aware datetimes; compare by calendar date.
    df.index = [ts.date() if hasattr(ts, "date") else ts for ts in df.index]

    if date(2024, 6, 7) not in df.index or date(2024, 6, 10) not in df.index:
        pytest.skip("NVDA bars missing 2024-06-07 or 2024-06-10 in returned range")

    close_07 = float(df.loc[date(2024, 6, 7), "close"])
    close_10 = float(df.loc[date(2024, 6, 10), "close"])
    ratio = close_10 / close_07

    # Adjusted: ratio should be near 1.0 (within a normal daily move).
    # Unadjusted (the bug): ratio ~ 0.10 because of the 10:1 split.
    assert 0.85 < ratio < 1.15, (
        f"NVDA close ratio 06-10/06-07 = {ratio:.4f}; expected ~1.0 (continuous adjusted "
        f"series). Got something split-shaped — is auto_adjust=True in src/data/loader.py?"
    )
