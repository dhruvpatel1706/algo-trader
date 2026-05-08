"""Funding-rate history buffer.

The ``funding_rate_divergence`` strategy needs a percentile distribution
of funding rates over a multi-week window to compute extreme values.
The public funding APIs only return ~100 records per call, which is
~33 days of 8h prints — not enough for a stable percentile, especially
once you bucket by symbol.

This script builds the buffer over time. On each run:
  1. Pull the last ~30 days of funding rate prints for every symbol in
     ``crypto_majors`` from Binance / Bybit / OKX (whichever venue is
     reachable on this network — see :func:`src.data.funding.fetch_funding_rate`).
  2. Append to ``data/funding_history.parquet`` with deduplication on
     ``(symbol, ts)``.
  3. Keep only the trailing ``--keep-days`` window (default 365) so the
     parquet doesn't grow unbounded.

Schedule it every 8 hours (matches the natural funding cadence on
Binance/OKX): one cron entry, six rows per call. Three months of
running gives ~2700 prints per symbol — more than enough sample for
the percentile thresholds the strategy uses.

Usage
-----

    uv run python scripts/funding_history_buffer.py           # one-shot
    uv run python scripts/funding_history_buffer.py --once    # alias
    uv run python scripts/funding_history_buffer.py --backfill 90
                                                # pull 90 days of history

The runner calls this function (not via subprocess) on its 8h
APScheduler cadence — see ``src/runtime/scheduler.py`` job
``funding_history_refresh``.

Operations
----------
Inspect the accumulated buffer with::

    uv run python -c "import pandas as pd; \\
        df = pd.read_parquet('data/funding_history.parquet'); \\
        print(df.groupby('symbol').size().sort_values(ascending=False))"
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

# Make ``src.*`` importable when run as a CLI script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data.funding import fetch_funding_rate  # noqa: E402
from src.data.universe import Universe  # noqa: E402

log = logging.getLogger("algo_trader.funding_history_buffer")

# Default trailing-history retention. One year matches the
# strategy's typical lookback for percentile thresholds; older prints
# are dropped when the buffer is rewritten.
_DEFAULT_KEEP_DAYS: int = 365

# Default per-call window. Each venue caps results around 100; 30 days
# is the sweet spot — enough overlap to dedupe gaps without burning
# requests on data we already have.
_DEFAULT_WINDOW_DAYS: int = 30

# Where the parquet lives. Lives under ``data/`` (not ``data/cache/``)
# because this is canonical persisted data — caches can be re-derived
# from upstream, this can not.
_BUFFER_PATH: Path = _REPO_ROOT / "data" / "funding_history.parquet"


def collect_once(
    symbols: list[str] | None = None,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    keep_days: int = _DEFAULT_KEEP_DAYS,
    buffer_path: Path = _BUFFER_PATH,
    asof: datetime | None = None,
) -> dict[str, int]:
    """Run one collection cycle. Returns ``{symbol: rows_added}``.

    Steps:
      1. Load the existing buffer (if any).
      2. For each symbol, fetch the last ``window_days`` of funding
         prints from whichever venue answers.
      3. Merge new rows into the buffer, deduplicate on
         ``(symbol, ts)``, drop anything older than ``keep_days``.
      4. Write the buffer back atomically (write-then-rename).

    Failures on individual symbols are logged and skipped — one bad
    symbol does not abort the whole cycle.
    """
    asof = asof or datetime.now(UTC)
    end_d = asof.date()
    start_d = end_d - timedelta(days=window_days)
    keep_cutoff = asof - timedelta(days=keep_days)

    if symbols is None:
        symbols = list(Universe.named("crypto_majors"))

    existing = _load_buffer(buffer_path)
    new_rows: list[pd.DataFrame] = []
    counts: dict[str, int] = {}

    for sym in symbols:
        try:
            df = fetch_funding_rate(sym, start_d, end_d)
        except Exception:
            log.exception("funding_history_buffer: fetch failed for %s", sym)
            counts[sym] = 0
            continue
        if df is None or df.empty:
            counts[sym] = 0
            continue
        # Normalise: index = UTC ts column, add ``symbol`` column. The
        # buffer stores all symbols in one parquet keyed by symbol.
        df = df.copy()
        df.index.name = "ts"
        df = df.reset_index()
        df["symbol"] = sym
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        new_rows.append(df[["symbol", "ts", "funding_rate", "predicted_rate"]])
        counts[sym] = len(df)

    if not new_rows and existing.empty:
        log.warning("funding_history_buffer: no new rows and no existing buffer; nothing to write")
        return counts

    merged = pd.concat([existing, *new_rows], ignore_index=True) if new_rows else existing
    # Dedup on (symbol, ts), keeping the most recent fetch's value for
    # each timestamp. The last-write-wins is correct because if a
    # venue revises a print between fetches, the newer value is the
    # canonical one.
    merged = merged.drop_duplicates(subset=("symbol", "ts"), keep="last")
    # Drop rows older than the retention window.
    merged = merged[merged["ts"] >= keep_cutoff]
    # Stable sort for reproducibility.
    merged = merged.sort_values(["symbol", "ts"]).reset_index(drop=True)

    _atomic_write(merged, buffer_path)
    log.info(
        "funding_history_buffer: wrote %d rows across %d symbols (kept since %s)",
        len(merged), merged["symbol"].nunique(), keep_cutoff.date().isoformat(),
    )
    return counts


def backfill(
    symbols: list[str] | None = None,
    *,
    days: int = 90,
    buffer_path: Path = _BUFFER_PATH,
) -> dict[str, int]:
    """Pull a longer history at boot to seed the buffer.

    The public funding APIs cap results around 100 per call, so the
    first one-shot collection only covers ~33 days even when we ask
    for 90. This function is intentionally a thin wrapper over
    :func:`collect_once` — it just calls the fetch with a longer
    window and lets the venues return whatever they give back.

    Useful when standing up the buffer for the first time, or when
    re-bootstrapping after an outage.
    """
    return collect_once(
        symbols, window_days=days, buffer_path=buffer_path,
    )


def _load_buffer(path: Path) -> pd.DataFrame:
    if not path.exists():
        return _empty_buffer()
    try:
        df = pd.read_parquet(path)
    except Exception:
        log.exception(
            "funding_history_buffer: failed to read existing buffer at %s; "
            "starting fresh", path,
        )
        return _empty_buffer()
    # Backwards-compat: if the file has fewer columns than we expect,
    # pad in the missing ones so the concat below doesn't NaN-pollute.
    for col in ("symbol", "ts", "funding_rate", "predicted_rate"):
        if col not in df.columns:
            df[col] = pd.NA
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df[["symbol", "ts", "funding_rate", "predicted_rate"]]


def _empty_buffer() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": pd.Series(dtype="string"),
            "ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "funding_rate": pd.Series(dtype="float64"),
            "predicted_rate": pd.Series(dtype="float64"),
        }
    )


def _atomic_write(df: pd.DataFrame, path: Path) -> None:
    """Write parquet to a sibling tmp file then rename. Atomic on POSIX
    filesystems — the buffer file is never half-written if the process
    is killed mid-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp)
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one collection cycle and exit (the default behaviour).",
    )
    parser.add_argument(
        "--backfill",
        type=int,
        metavar="DAYS",
        help="Initial backfill window — pulls this many days of history then exits.",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=_DEFAULT_KEEP_DAYS,
        help=f"Trailing retention window in days. Default: {_DEFAULT_KEEP_DAYS}.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=_DEFAULT_WINDOW_DAYS,
        help=f"Fetch window per call. Default: {_DEFAULT_WINDOW_DAYS}.",
    )
    parser.add_argument(
        "--buffer-path",
        type=Path,
        default=_BUFFER_PATH,
        help=f"Where to read/write the parquet. Default: {_BUFFER_PATH}.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.backfill:
        counts = backfill(days=args.backfill, buffer_path=args.buffer_path)
    else:
        counts = collect_once(
            window_days=args.window_days,
            keep_days=args.keep_days,
            buffer_path=args.buffer_path,
        )

    total = sum(counts.values())
    print(f"funding_history_buffer: collected {total} rows across {len(counts)} symbols")
    for sym, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {sym:>10s}: {n:>4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
