"""Tests for scripts.funding_history_buffer.

The buffer is a thin orchestration layer over
:func:`src.data.funding.fetch_funding_rate`. The fetcher is patched
so tests never touch the network. We pin:
  - First-run on an empty buffer: writes the parquet with the expected schema.
  - Subsequent runs: deduplicate on ``(symbol, ts)`` (same rate -> idempotent).
  - Subsequent runs: append new rows when later prints arrive.
  - Trailing-window retention: rows older than ``keep_days`` are dropped.
  - Symbol-level fetch failures are caught and reported via the counts dict.
  - Atomic write: the parquet is never half-written if the process is killed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from scripts.funding_history_buffer import collect_once


def _make_funding_df(start_ts: datetime, periods: int) -> pd.DataFrame:
    """Build a fake funding-rate frame matching what
    src.data.funding.fetch_funding_rate returns: index = UTC ts,
    columns = ['funding_rate', 'predicted_rate']."""
    idx = pd.date_range(start_ts, periods=periods, freq="8h", tz="UTC")
    return pd.DataFrame(
        {
            "funding_rate": [0.0001 * i for i in range(periods)],
            "predicted_rate": [0.00012 * i for i in range(periods)],
        },
        index=idx,
    )


def test_first_run_creates_parquet_with_expected_schema(monkeypatch, tmp_path):
    """Empty buffer + first collection → parquet written with the four
    expected columns and one row per fetched timestamp per symbol."""
    asof = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)

    def fake_fetch(symbol, start, end, **kwargs):
        return _make_funding_df(asof - timedelta(days=3), periods=9)

    monkeypatch.setattr(
        "scripts.funding_history_buffer.fetch_funding_rate", fake_fetch
    )
    buf = tmp_path / "funding_history.parquet"

    counts = collect_once(
        symbols=["BTCUSDT", "ETHUSDT"],
        window_days=7,
        keep_days=365,
        buffer_path=buf,
        asof=asof,
    )

    assert counts == {"BTCUSDT": 9, "ETHUSDT": 9}
    assert buf.exists(), "buffer parquet must be written on first run"

    df = pd.read_parquet(buf)
    assert list(df.columns) == ["symbol", "ts", "funding_rate", "predicted_rate"]
    assert len(df) == 18  # 2 symbols * 9 timestamps
    assert set(df["symbol"].unique()) == {"BTCUSDT", "ETHUSDT"}


def test_second_run_dedupes_overlapping_timestamps(monkeypatch, tmp_path):
    """A second collection that returns OVERLAPPING timestamps (same
    venue, same symbols, same window) must NOT duplicate rows. Pinning
    this prevents the buffer from growing linearly with the number of
    cron runs."""
    asof = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)

    def fake_fetch(symbol, start, end, **kwargs):
        return _make_funding_df(asof - timedelta(days=3), periods=9)

    monkeypatch.setattr(
        "scripts.funding_history_buffer.fetch_funding_rate", fake_fetch
    )
    buf = tmp_path / "funding_history.parquet"

    collect_once(
        symbols=["BTCUSDT"], window_days=7, buffer_path=buf, asof=asof,
    )
    collect_once(
        symbols=["BTCUSDT"], window_days=7, buffer_path=buf, asof=asof,
    )

    df = pd.read_parquet(buf)
    # Same 9 timestamps, dedup on (symbol, ts) → still 9 rows.
    assert len(df) == 9


def test_second_run_appends_newer_timestamps(monkeypatch, tmp_path):
    """A later run should add NEW timestamps (one fresh funding print)
    on top of the existing rows."""
    base_asof = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    later_asof = base_asof + timedelta(hours=8)

    fetched_args = []

    def fake_fetch(symbol, start, end, **kwargs):
        fetched_args.append((symbol, start, end))
        # Fetch returns prints up to whichever asof is current — for
        # the test we just bake in 9 prints anchored at base_asof and
        # one extra anchored at later_asof.
        if any("later_run" in str(arg) for arg in [start, end]):
            return _make_funding_df(later_asof, periods=1)
        # First run: 9 prints in the past 3 days.
        return _make_funding_df(base_asof - timedelta(days=3), periods=9)

    monkeypatch.setattr(
        "scripts.funding_history_buffer.fetch_funding_rate", fake_fetch
    )
    buf = tmp_path / "funding_history.parquet"

    collect_once(
        symbols=["BTCUSDT"], window_days=7, buffer_path=buf, asof=base_asof,
    )

    # Now patch a different fetch that returns fresh + overlap rows.
    def fake_fetch_2(symbol, start, end, **kwargs):
        # Returns base prints + one new at later_asof (10 total).
        df = _make_funding_df(base_asof - timedelta(days=3), periods=10)
        return df

    monkeypatch.setattr(
        "scripts.funding_history_buffer.fetch_funding_rate", fake_fetch_2
    )
    collect_once(
        symbols=["BTCUSDT"], window_days=7, buffer_path=buf, asof=later_asof,
    )

    df = pd.read_parquet(buf)
    # Dedup on (symbol, ts): 9 overlap + 1 new = 10 rows.
    assert len(df) == 10


def test_keep_days_drops_old_rows(monkeypatch, tmp_path):
    """Rows older than ``keep_days`` must be evicted on each write to
    keep the parquet bounded."""
    asof = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)

    def fake_fetch(symbol, start, end, **kwargs):
        # Spread 12 prints over a year — half are inside the 30d
        # retention window, half are older and must be evicted.
        idx = pd.date_range(asof - timedelta(days=300), periods=12, freq="30D", tz="UTC")
        return pd.DataFrame(
            {
                "funding_rate": [0.0001 * i for i in range(12)],
                "predicted_rate": [0.00012 * i for i in range(12)],
            },
            index=idx,
        )

    monkeypatch.setattr(
        "scripts.funding_history_buffer.fetch_funding_rate", fake_fetch
    )
    buf = tmp_path / "funding_history.parquet"

    collect_once(
        symbols=["BTCUSDT"], window_days=365, keep_days=30, buffer_path=buf, asof=asof,
    )

    df = pd.read_parquet(buf)
    cutoff = asof - timedelta(days=30)
    assert (df["ts"] >= cutoff).all(), "rows older than keep_days must be dropped"


def test_per_symbol_fetch_failure_does_not_abort_cycle(monkeypatch, tmp_path):
    """If one symbol's fetch raises, the others must still be persisted."""
    asof = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)

    def fake_fetch(symbol, start, end, **kwargs):
        if symbol == "BROKEN":
            raise RuntimeError("upstream 503")
        return _make_funding_df(asof - timedelta(days=1), periods=3)

    monkeypatch.setattr(
        "scripts.funding_history_buffer.fetch_funding_rate", fake_fetch
    )
    buf = tmp_path / "funding_history.parquet"

    counts = collect_once(
        symbols=["BTCUSDT", "BROKEN", "ETHUSDT"],
        window_days=7,
        buffer_path=buf,
        asof=asof,
    )
    assert counts["BROKEN"] == 0
    assert counts["BTCUSDT"] == 3
    assert counts["ETHUSDT"] == 3
    df = pd.read_parquet(buf)
    assert "BROKEN" not in set(df["symbol"].unique())
    assert {"BTCUSDT", "ETHUSDT"}.issubset(set(df["symbol"].unique()))


def test_empty_fetch_returns_zero_count(monkeypatch, tmp_path):
    """Venue returns empty frame -> count is 0, no rows written."""
    monkeypatch.setattr(
        "scripts.funding_history_buffer.fetch_funding_rate",
        lambda symbol, start, end, **kw: pd.DataFrame(
            columns=["funding_rate", "predicted_rate"]
        ),
    )
    buf = tmp_path / "funding_history.parquet"
    counts = collect_once(
        symbols=["BTCUSDT"], buffer_path=buf,
        asof=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
    )
    assert counts == {"BTCUSDT": 0}


def test_atomic_write_uses_tmp_then_rename(monkeypatch, tmp_path):
    """The buffer must be written via ``foo.parquet.tmp -> foo.parquet``
    so a kill mid-write doesn't leave a half-written parquet."""
    asof = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "scripts.funding_history_buffer.fetch_funding_rate",
        lambda symbol, start, end, **kw: _make_funding_df(
            asof - timedelta(days=1), periods=2
        ),
    )
    buf = tmp_path / "funding_history.parquet"

    rename_calls = []
    real_replace = Path.replace

    def spy_replace(self, target):
        rename_calls.append((self, target))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy_replace)
    collect_once(symbols=["BTCUSDT"], buffer_path=buf, asof=asof)

    assert len(rename_calls) == 1
    src_path, dst_path = rename_calls[0]
    assert str(src_path).endswith(".tmp")
    assert dst_path == buf
