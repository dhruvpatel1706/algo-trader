"""Universal in-memory cache of daily OHLCV bars.

The runtime fetches bars on a periodic ``data_refresh`` job. Strategy
evaluation pulls from this cache rather than re-fetching every cycle. The
cache dispatches by :class:`~src.agents.base.AssetClass` so a single
instance serves equity, crypto, and any future asset classes (gold /
silver / bonds use the equity loader since GLD / SLV / TLT are NYSE-listed
ETFs and share the daily-bar contract).

Thread-safety
-------------
:meth:`BarsCache.refresh` may run on the ``data_refresh`` job thread while
:meth:`BarsCache.get_for` runs on an ``agent.eval`` thread. All operations
are guarded by a single :class:`threading.RLock`. :meth:`get_for` returns a
*shallow copy* of the underlying dict so callers can mutate it freely
without poisoning cache state. Underlying ``DataFrame`` objects are not
deep-copied — strategies are expected to treat them as read-only.

Failure semantics
-----------------
If a loader raises, the previous bars stay in place and ``last_refresh_ts``
is **not** advanced. Downstream callers will see :meth:`is_stale_for`
flip to ``True`` once the TTL expires, signalling that data may be stale
and that a halt-on-stale-data policy might apply.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

from src.agents.base import AssetClass

# The loader signatures we depend on. Kept as plain ``Callable`` aliases —
# the real loaders in ``src.data.loader`` take ``date`` arguments, but the
# cache passes ``datetime`` (a subclass of ``date``) so equality / range
# comparisons inside the loaders still work. Tests inject simpler mocks.
EquityLoader = Callable[[list[str], "datetime", "datetime"], dict[str, "pd.DataFrame"]]
CryptoLoader = Callable[[list[str], "datetime", "datetime", str], dict[str, "pd.DataFrame"]]


log = logging.getLogger("algo_trader.bars_cache")


# Asset classes that resolve to NYSE-listed equities / ETFs and therefore
# go through the equity loader. Kept as a frozenset so membership tests are
# O(1) and the value is immutable.
_EQUITY_LIKE: frozenset[AssetClass] = frozenset(
    {
        AssetClass.EQUITY,
        AssetClass.GOLD,
        AssetClass.SILVER,
        AssetClass.BONDS,
    }
)


@dataclass(slots=True)
class CacheStats:
    """Snapshot of one asset class's cache state.

    Attributes:
        asset_class: Which asset bucket this snapshot describes.
        last_refresh_ts: Timestamp of the last *successful* refresh, or
            ``None`` if the asset class has never been refreshed.
        n_symbols: Count of distinct symbols cached for this asset class.
        rows_per_symbol_min: Minimum row count across cached symbols, or
            ``0`` when no symbols are cached.
        rows_per_symbol_max: Maximum row count across cached symbols, or
            ``0`` when no symbols are cached.
        is_stale: ``True`` when the data is older than the TTL or the
            asset class has never been refreshed.
    """

    asset_class: AssetClass
    last_refresh_ts: datetime | None
    n_symbols: int
    rows_per_symbol_min: int
    rows_per_symbol_max: int
    is_stale: bool


class BarsCache:
    """Thread-safe in-memory cache of daily OHLCV bars.

    A single instance serves every asset class; loader dispatch is keyed
    on :class:`~src.agents.base.AssetClass`. The cache is intentionally a
    small piece of glue — it fetches via the injected loaders, stores per
    symbol, and exposes ``get_for(universe)`` for downstream consumers.

    Args:
        equity_loader: Callable used for ``EQUITY``, ``GOLD``, ``SILVER``,
            ``BONDS``. When ``None``, defaults to a lazy import of
            :func:`src.data.loader.load_daily_bars`.
        crypto_loader: Callable used for ``CRYPTO``. When ``None``,
            defaults to a lazy import of
            :func:`src.data.loader.load_crypto_bars`.
        equity_lookback_days: Number of trailing calendar days to fetch
            on each equity refresh. Defaults to 300 (enough for a 200 SMA
            plus warmup).
        crypto_lookback_days: Number of trailing calendar days to fetch
            on each crypto refresh. Defaults to 365.
        equity_ttl_seconds: After this many seconds since the last
            successful equity refresh, :meth:`is_stale_for` reports
            ``True``. Defaults to 300 (5 minutes).
        crypto_ttl_seconds: Same idea for crypto. Defaults to 60 because
            the crypto runner cadence is 15 minutes and we want bars
            fresh enough to be useful within that window.
        clock: Injectable clock for tests. Defaults to a UTC ``datetime``
            getter.
    """

    def __init__(
        self,
        *,
        equity_loader: EquityLoader | None = None,
        crypto_loader: CryptoLoader | None = None,
        equity_lookback_days: int = 300,
        crypto_lookback_days: int = 365,
        equity_ttl_seconds: int = 300,
        crypto_ttl_seconds: int = 60,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._equity_loader = equity_loader
        self._crypto_loader = crypto_loader
        self._equity_lookback_days = equity_lookback_days
        self._crypto_lookback_days = crypto_lookback_days
        self._equity_ttl_seconds = equity_ttl_seconds
        self._crypto_ttl_seconds = crypto_ttl_seconds
        self._clock = clock

        self._lock = RLock()
        # Flat ``{symbol: DataFrame}``. Symbols belong to whichever asset
        # class wrote them last; we don't expect collisions because the
        # universes are disjoint by construction.
        self._bars: dict[str, pd.DataFrame] = {}
        # Per-asset-class metadata.
        self._last_refresh_ts: dict[AssetClass, datetime] = {}
        self._symbols_by_class: dict[AssetClass, set[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def refresh(
        self,
        asset_class: AssetClass,
        universe: tuple[str, ...] | list[str],
    ) -> CacheStats:
        """Fetch fresh bars for ``universe`` and merge into the cache.

        Idempotent — calling repeatedly with the same universe re-fetches
        and overwrites in place. Governance is a no-op (it doesn't trade
        market instruments). Unknown asset classes raise ``ValueError``.

        Args:
            asset_class: Which loader to dispatch to.
            universe: Symbols to fetch.

        Returns:
            :class:`CacheStats` describing the post-refresh state.

        Raises:
            ValueError: If ``asset_class`` is not a recognised
                :class:`~src.agents.base.AssetClass` member.
        """
        symbols = list(universe)

        # Governance never trades; refresh is a documented no-op.
        if asset_class is AssetClass.GOVERNANCE:
            return self.stats(asset_class)

        if asset_class in _EQUITY_LIKE:
            self._refresh_equity_like(asset_class, symbols)
        elif asset_class is AssetClass.CRYPTO:
            self._refresh_crypto(asset_class, symbols)
        else:  # pragma: no cover - defence-in-depth for future enum members
            raise ValueError(f"unknown asset_class: {asset_class!r}")

        return self.stats(asset_class)

    def get_for(self, universe: tuple[str, ...] | list[str]) -> dict[str, pd.DataFrame]:
        """Return a shallow-copied ``{symbol: bars_df}`` dict.

        Symbols that aren't cached are silently skipped — the caller
        should compare ``len(result) == len(universe)`` if completeness
        matters. Returns an empty dict when nothing is cached or
        ``universe`` is empty.

        Args:
            universe: Symbols of interest.

        Returns:
            A new dict mapping each requested symbol that exists in the
            cache to its cached ``DataFrame``. Mutating the returned dict
            does not affect cache state.
        """
        with self._lock:
            return {sym: self._bars[sym] for sym in universe if sym in self._bars}

    def is_stale_for(self, asset_class: AssetClass) -> bool:
        """Return ``True`` when the last refresh exceeded the TTL.

        ``True`` if the asset class has never been refreshed. Governance
        is reported as fresh (it has no data to be stale about).

        Args:
            asset_class: Asset class to query.

        Returns:
            ``True`` when stale or never refreshed; ``False`` otherwise.
        """
        if asset_class is AssetClass.GOVERNANCE:
            return False

        with self._lock:
            last = self._last_refresh_ts.get(asset_class)
            if last is None:
                return True
            ttl = self._ttl_seconds_for(asset_class)
            return (self._clock() - last) > timedelta(seconds=ttl)

    def stats(self, asset_class: AssetClass) -> CacheStats:
        """Inspection snapshot for one asset class.

        Useful for the dashboard heartbeat panel and for unit tests.

        Args:
            asset_class: Which asset bucket to describe.

        Returns:
            A :class:`CacheStats` value object.
        """
        with self._lock:
            symbols = self._symbols_by_class.get(asset_class, set())
            row_counts = [len(self._bars[s]) for s in symbols if s in self._bars]
            n = len(symbols)
            row_min = min(row_counts) if row_counts else 0
            row_max = max(row_counts) if row_counts else 0
            return CacheStats(
                asset_class=asset_class,
                last_refresh_ts=self._last_refresh_ts.get(asset_class),
                n_symbols=n,
                rows_per_symbol_min=row_min,
                rows_per_symbol_max=row_max,
                is_stale=self.is_stale_for(asset_class),
            )

    def clear(self) -> None:
        """Drop every cached symbol and reset all per-class metadata."""
        with self._lock:
            self._bars.clear()
            self._last_refresh_ts.clear()
            self._symbols_by_class.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _ttl_seconds_for(self, asset_class: AssetClass) -> int:
        """Return the TTL seconds applicable to ``asset_class``."""
        if asset_class is AssetClass.CRYPTO:
            return self._crypto_ttl_seconds
        return self._equity_ttl_seconds

    def _refresh_equity_like(
        self, asset_class: AssetClass, symbols: list[str]
    ) -> None:
        """Fetch ``symbols`` via the equity loader and merge into cache."""
        loader = self._equity_loader
        if loader is None:
            # Lazy import so tests can inject a mock without paying the
            # alpaca/yfinance import cost.
            from src.data.loader import load_daily_bars  # noqa: PLC0415

            loader = load_daily_bars  # type: ignore[assignment]

        # ``load_daily_bars`` and ``load_crypto_bars`` are both ``date``-typed
        # (their internal cache-coverage checks compare via ``date.__le__``);
        # passing ``datetime`` raises ``TypeError`` deep in pandas. Convert
        # at the boundary so the cache always speaks the loader's API.
        now_dt = self._clock()
        end_d = now_dt.date()
        start_d = end_d - timedelta(days=self._equity_lookback_days)
        self._invoke_and_store(asset_class, symbols, loader, (symbols, start_d, end_d))

    def _refresh_crypto(self, asset_class: AssetClass, symbols: list[str]) -> None:
        """Fetch ``symbols`` via the crypto loader and merge into cache."""
        loader = self._crypto_loader
        if loader is None:
            # Lazy import: see _refresh_equity_like for rationale.
            from src.data.loader import load_crypto_bars  # noqa: PLC0415

            loader = load_crypto_bars  # type: ignore[assignment]

        now_dt = self._clock()
        end_d = now_dt.date()
        start_d = end_d - timedelta(days=self._crypto_lookback_days)
        # The crypto loader takes an extra ``interval`` argument; we pin
        # to daily bars here so the cache contract is uniform.
        self._invoke_and_store(asset_class, symbols, loader, (symbols, start_d, end_d, "1d"))

    def _invoke_and_store(
        self,
        asset_class: AssetClass,
        symbols: list[str],
        loader: Callable[..., dict[str, pd.DataFrame]],
        args: tuple[object, ...],
    ) -> None:
        """Call ``loader(*args)``, merge result, advance ``last_refresh_ts``.

        Loader exceptions are logged and swallowed: existing cached bars
        remain untouched and ``last_refresh_ts`` is **not** advanced, so
        ``is_stale_for`` will flip to ``True`` once the TTL expires.
        """
        try:
            fetched = loader(*args)
        except Exception:
            log.exception(
                "bar loader failed for %s (universe=%s); cache untouched",
                asset_class,
                symbols,
            )
            return

        if not isinstance(fetched, dict):
            log.warning(
                "loader returned non-dict for %s; treating as empty",
                asset_class,
            )
            fetched = {}

        if not fetched:
            log.warning(
                "loader returned no bars for %s (universe=%s)",
                asset_class,
                symbols,
            )

        with self._lock:
            for sym, df in fetched.items():
                self._bars[sym] = df
            cls_set = self._symbols_by_class.setdefault(asset_class, set())
            cls_set.update(fetched.keys())
            # Only advance the timestamp on a successful loader call.
            self._last_refresh_ts[asset_class] = self._clock()
