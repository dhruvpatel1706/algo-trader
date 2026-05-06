"""Unit tests for ``src.runtime.bars_cache.BarsCache``.

These tests pin the loader-dispatch contract, the staleness / TTL math,
the failure semantics (loader exceptions must not poison the cache), and
the thread-safety contract of concurrent reads during a refresh.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pytest
from src.agents.base import AssetClass
from src.runtime.bars_cache import BarsCache, CacheStats

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_df(n_rows: int = 5, *, base: float = 100.0) -> pd.DataFrame:
    """Build a tiny OHLCV DataFrame with ``n_rows`` daily bars."""
    idx = pd.date_range("2025-01-01", periods=n_rows, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": [base + i for i in range(n_rows)],
            "high": [base + 1 + i for i in range(n_rows)],
            "low": [base - 1 + i for i in range(n_rows)],
            "close": [base + 0.5 + i for i in range(n_rows)],
            "volume": [1_000 + 10 * i for i in range(n_rows)],
        },
        index=idx,
    )


@dataclass
class _RecordingLoader:
    """Callable that records its call args and returns canned bars."""

    bars_per_symbol: dict[str, pd.DataFrame] = field(default_factory=dict)
    raise_on_call: BaseException | None = None
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def __call__(self, *args: Any) -> dict[str, pd.DataFrame]:
        self.calls.append(args)
        if self.raise_on_call is not None:
            raise self.raise_on_call
        symbols = args[0]
        return {s: self.bars_per_symbol[s] for s in symbols if s in self.bars_per_symbol}


@dataclass
class _ManualClock:
    """Settable clock so TTL behaviour is deterministic."""

    now: datetime = field(default_factory=lambda: datetime(2025, 6, 1, 12, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: Any) -> None:
        self.now = self.now + timedelta(**kwargs)


@pytest.fixture
def equity_loader() -> _RecordingLoader:
    return _RecordingLoader(
        bars_per_symbol={
            "SPY": _make_df(10),
            "QQQ": _make_df(10),
            "GLD": _make_df(10),
        }
    )


@pytest.fixture
def crypto_loader() -> _RecordingLoader:
    return _RecordingLoader(
        bars_per_symbol={
            "BTCUSDT": _make_df(20),
            "ETHUSDT": _make_df(20),
        }
    )


@pytest.fixture
def clock() -> _ManualClock:
    return _ManualClock()


@pytest.fixture
def cache(
    equity_loader: _RecordingLoader,
    crypto_loader: _RecordingLoader,
    clock: _ManualClock,
) -> BarsCache:
    return BarsCache(
        equity_loader=equity_loader,
        crypto_loader=crypto_loader,
        equity_lookback_days=300,
        crypto_lookback_days=365,
        equity_ttl_seconds=300,
        crypto_ttl_seconds=60,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# Loader dispatch
# ---------------------------------------------------------------------------


def test_refresh_equity_calls_loader_with_correct_window(
    cache: BarsCache, equity_loader: _RecordingLoader, clock: _ManualClock
) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY", "QQQ"))

    assert len(equity_loader.calls) == 1
    symbols, start, end = equity_loader.calls[0]
    assert symbols == ["SPY", "QQQ"]
    # The cache passes dates (loader API), not datetimes; keep the assertion
    # aligned with that contract so a regression in the conversion is caught.
    assert end == clock.now.date()
    assert start == clock.now.date() - timedelta(days=300)


@pytest.mark.parametrize(
    "asset_class",
    [AssetClass.GOLD, AssetClass.SILVER, AssetClass.BONDS],
)
def test_equity_like_classes_use_equity_loader(
    cache: BarsCache,
    equity_loader: _RecordingLoader,
    crypto_loader: _RecordingLoader,
    asset_class: AssetClass,
) -> None:
    cache.refresh(asset_class, ("GLD",))
    assert len(equity_loader.calls) == 1
    assert len(crypto_loader.calls) == 0


def test_refresh_crypto_calls_loader_with_daily_interval(
    cache: BarsCache, crypto_loader: _RecordingLoader, clock: _ManualClock
) -> None:
    cache.refresh(AssetClass.CRYPTO, ("BTCUSDT",))

    assert len(crypto_loader.calls) == 1
    args = crypto_loader.calls[0]
    assert len(args) == 4
    symbols, start, end, interval = args
    assert symbols == ["BTCUSDT"]
    assert interval == "1d"
    # date-typed window (loader API); see equity test for rationale.
    assert end == clock.now.date()
    assert start == clock.now.date() - timedelta(days=365)


def test_refresh_governance_is_no_op(
    cache: BarsCache,
    equity_loader: _RecordingLoader,
    crypto_loader: _RecordingLoader,
) -> None:
    stats = cache.refresh(AssetClass.GOVERNANCE, ("anything",))
    assert equity_loader.calls == []
    assert crypto_loader.calls == []
    assert stats.asset_class is AssetClass.GOVERNANCE
    assert stats.n_symbols == 0
    # Governance never trades, so it should never be flagged stale.
    assert stats.is_stale is False


def test_refresh_unknown_asset_class_raises_value_error(
    cache: BarsCache,
) -> None:
    """A non-AssetClass value triggers the ValueError guard."""

    class _NotAnAssetClass:
        pass

    with pytest.raises(ValueError, match="unknown asset_class"):
        cache.refresh(_NotAnAssetClass(), ("SPY",))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_for behaviour
# ---------------------------------------------------------------------------


def test_get_for_returns_only_requested_symbols(
    cache: BarsCache, equity_loader: _RecordingLoader
) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY", "QQQ", "GLD"))
    out = cache.get_for(("SPY", "QQQ", "TLT"))  # TLT not cached
    assert set(out.keys()) == {"SPY", "QQQ"}


def test_get_for_returns_shallow_copy_not_mutating_cache(cache: BarsCache) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    snapshot = cache.get_for(("SPY",))
    snapshot["INJECTED"] = _make_df(3)
    snapshot.pop("SPY")

    fresh = cache.get_for(("SPY", "INJECTED"))
    assert "SPY" in fresh
    assert "INJECTED" not in fresh


def test_get_for_empty_universe_returns_empty_dict(cache: BarsCache) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    assert cache.get_for(()) == {}
    assert cache.get_for([]) == {}


def test_get_for_with_no_cache_returns_empty_dict_not_crash(cache: BarsCache) -> None:
    out = cache.get_for(("SPY", "QQQ"))
    assert out == {}


def test_get_for_accepts_list_or_tuple(cache: BarsCache) -> None:
    cache.refresh(AssetClass.EQUITY, ["SPY", "QQQ"])
    via_list = cache.get_for(["SPY"])
    via_tuple = cache.get_for(("SPY",))
    assert set(via_list.keys()) == set(via_tuple.keys()) == {"SPY"}


# ---------------------------------------------------------------------------
# Staleness / TTL
# ---------------------------------------------------------------------------


def test_is_stale_for_returns_true_before_first_refresh(cache: BarsCache) -> None:
    assert cache.is_stale_for(AssetClass.EQUITY) is True
    assert cache.is_stale_for(AssetClass.CRYPTO) is True


def test_is_stale_for_returns_false_immediately_after_refresh(
    cache: BarsCache,
) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    assert cache.is_stale_for(AssetClass.EQUITY) is False


def test_is_stale_for_returns_true_after_ttl_expires(
    cache: BarsCache, clock: _ManualClock
) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    assert cache.is_stale_for(AssetClass.EQUITY) is False

    # Equity TTL is 300s — push past it.
    clock.advance(seconds=301)
    assert cache.is_stale_for(AssetClass.EQUITY) is True


def test_is_stale_for_uses_per_class_ttl(
    cache: BarsCache, clock: _ManualClock
) -> None:
    """Crypto TTL (60s) is shorter than equity TTL (300s)."""
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    cache.refresh(AssetClass.CRYPTO, ("BTCUSDT",))

    clock.advance(seconds=90)  # past crypto TTL, well within equity TTL
    assert cache.is_stale_for(AssetClass.CRYPTO) is True
    assert cache.is_stale_for(AssetClass.EQUITY) is False


def test_governance_is_never_stale(cache: BarsCache, clock: _ManualClock) -> None:
    clock.advance(days=365)
    assert cache.is_stale_for(AssetClass.GOVERNANCE) is False


# ---------------------------------------------------------------------------
# Failure semantics
# ---------------------------------------------------------------------------


def test_loader_exception_does_not_clear_existing_cache(
    cache: BarsCache, equity_loader: _RecordingLoader
) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    assert "SPY" in cache.get_for(("SPY",))

    equity_loader.raise_on_call = RuntimeError("boom")
    cache.refresh(AssetClass.EQUITY, ("QQQ",))

    # SPY survives even though the second refresh blew up.
    assert "SPY" in cache.get_for(("SPY",))


def test_loader_exception_does_not_advance_last_refresh_ts(
    cache: BarsCache,
    equity_loader: _RecordingLoader,
    clock: _ManualClock,
) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    first_ts = cache.stats(AssetClass.EQUITY).last_refresh_ts
    assert first_ts is not None

    clock.advance(seconds=30)
    equity_loader.raise_on_call = RuntimeError("boom")
    cache.refresh(AssetClass.EQUITY, ("QQQ",))

    after = cache.stats(AssetClass.EQUITY)
    assert after.last_refresh_ts == first_ts  # unchanged


def test_loader_exception_eventually_marks_cache_stale(
    cache: BarsCache,
    equity_loader: _RecordingLoader,
    clock: _ManualClock,
) -> None:
    """If the loader keeps failing, is_stale flips to True after the TTL."""
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    equity_loader.raise_on_call = RuntimeError("boom")

    # Try to refresh, but fail. Then advance past TTL.
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    clock.advance(seconds=301)
    assert cache.is_stale_for(AssetClass.EQUITY) is True


def test_empty_loader_result_does_not_crash(
    cache: BarsCache, equity_loader: _RecordingLoader, caplog: pytest.LogCaptureFixture
) -> None:
    equity_loader.bars_per_symbol = {}
    with caplog.at_level("WARNING", logger="algo_trader.bars_cache"):
        stats = cache.refresh(AssetClass.EQUITY, ("SPY",))
    assert stats.n_symbols == 0
    # Empty result is still considered a successful refresh.
    assert stats.last_refresh_ts is not None
    assert any("no bars" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Stats / clear
# ---------------------------------------------------------------------------


def test_stats_reports_correct_counts_after_refresh(cache: BarsCache) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY", "QQQ"))
    stats = cache.stats(AssetClass.EQUITY)
    assert isinstance(stats, CacheStats)
    assert stats.asset_class is AssetClass.EQUITY
    assert stats.n_symbols == 2
    assert stats.rows_per_symbol_min == 10
    assert stats.rows_per_symbol_max == 10
    assert stats.is_stale is False


def test_stats_for_unrefreshed_class_is_empty(cache: BarsCache) -> None:
    stats = cache.stats(AssetClass.CRYPTO)
    assert stats.asset_class is AssetClass.CRYPTO
    assert stats.n_symbols == 0
    assert stats.last_refresh_ts is None
    assert stats.rows_per_symbol_min == 0
    assert stats.rows_per_symbol_max == 0
    assert stats.is_stale is True


def test_clear_resets_everything(
    cache: BarsCache, equity_loader: _RecordingLoader
) -> None:
    cache.refresh(AssetClass.EQUITY, ("SPY", "QQQ"))
    cache.refresh(AssetClass.CRYPTO, ("BTCUSDT",))
    assert cache.get_for(("SPY", "BTCUSDT")) != {}

    cache.clear()
    assert cache.get_for(("SPY", "QQQ", "BTCUSDT")) == {}
    assert cache.is_stale_for(AssetClass.EQUITY) is True
    assert cache.is_stale_for(AssetClass.CRYPTO) is True
    assert cache.stats(AssetClass.EQUITY).last_refresh_ts is None


# ---------------------------------------------------------------------------
# Default loader injection
# ---------------------------------------------------------------------------


def test_default_loaders_are_imported_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no loader is injected, ``src.data.loader.load_daily_bars`` is used."""
    import src.data.loader as data_loader

    captured: dict[str, Any] = {}

    def fake_load_daily_bars(
        symbols: Any, start: Any, end: Any, **_: Any
    ) -> dict[str, pd.DataFrame]:
        captured["symbols"] = list(symbols)
        captured["start"] = start
        captured["end"] = end
        return {s: _make_df(3) for s in symbols}

    monkeypatch.setattr(data_loader, "load_daily_bars", fake_load_daily_bars)

    cache = BarsCache()  # no equity_loader injected
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    assert captured["symbols"] == ["SPY"]
    assert "SPY" in cache.get_for(("SPY",))


def test_default_crypto_loader_imports_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.data.loader as data_loader

    captured: dict[str, Any] = {}

    def fake_load_crypto_bars(
        symbols: Any, start: Any, end: Any, interval: str, **_: Any
    ) -> dict[str, pd.DataFrame]:
        captured["symbols"] = list(symbols)
        captured["interval"] = interval
        return {s: _make_df(3) for s in symbols}

    monkeypatch.setattr(data_loader, "load_crypto_bars", fake_load_crypto_bars)

    cache = BarsCache()  # no crypto_loader injected
    cache.refresh(AssetClass.CRYPTO, ("BTCUSDT",))
    assert captured["symbols"] == ["BTCUSDT"]
    assert captured["interval"] == "1d"


# ---------------------------------------------------------------------------
# Thread-safety
# ---------------------------------------------------------------------------


def test_thread_safety_concurrent_get_during_refresh() -> None:
    """10 reader threads run continuously while a writer thread refreshes
    repeatedly. Reads must never crash, and the final state must be the
    fully-populated dict.
    """

    bars_a = _make_df(50)
    bars_b = _make_df(50)

    def slow_loader(
        symbols: list[str], _start: datetime, _end: datetime
    ) -> dict[str, pd.DataFrame]:
        # A small sleep here yields the GIL and gives readers a chance to
        # interleave with the merge step inside the cache.
        import time

        time.sleep(0.001)
        return {"A": bars_a, "B": bars_b}

    cache = BarsCache(equity_loader=slow_loader)

    errors: list[BaseException] = []
    results: list[set[str]] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                got = cache.get_for(("A", "B"))
                results.append(set(got.keys()))
        except BaseException as e:
            errors.append(e)

    def writer() -> None:
        try:
            for _ in range(20):
                cache.refresh(AssetClass.EQUITY, ("A", "B"))
        except BaseException as e:
            errors.append(e)

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(10)]
    for t in readers:
        t.start()

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    writer_thread.join(timeout=10)
    stop.set()
    for t in readers:
        t.join(timeout=2)

    assert errors == []
    # Final state: A and B must both be present.
    final = cache.get_for(("A", "B"))
    assert set(final.keys()) == {"A", "B"}
    # Every observed read returned a coherent subset (either {} before
    # the first merge or with both keys after). Crucially, nothing
    # crashed.
    valid_states: list[set[str]] = [set(), {"A"}, {"B"}, {"A", "B"}]
    for snap in results:
        assert snap in valid_states, snap


def test_concurrent_refreshes_do_not_corrupt_state() -> None:
    """Two concurrent refresh() calls on different asset classes must
    both complete without exceptions."""
    eq_loader = _RecordingLoader(bars_per_symbol={"SPY": _make_df(5), "QQQ": _make_df(5)})
    cr_loader = _RecordingLoader(bars_per_symbol={"BTCUSDT": _make_df(5)})
    cache = BarsCache(equity_loader=eq_loader, crypto_loader=cr_loader)

    errors: list[BaseException] = []

    def go_eq() -> None:
        try:
            for _ in range(50):
                cache.refresh(AssetClass.EQUITY, ("SPY", "QQQ"))
        except BaseException as e:
            errors.append(e)

    def go_cr() -> None:
        try:
            for _ in range(50):
                cache.refresh(AssetClass.CRYPTO, ("BTCUSDT",))
        except BaseException as e:
            errors.append(e)

    threads = [
        threading.Thread(target=go_eq),
        threading.Thread(target=go_cr),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert errors == []
    out = cache.get_for(("SPY", "QQQ", "BTCUSDT"))
    assert set(out.keys()) == {"SPY", "QQQ", "BTCUSDT"}


# ---------------------------------------------------------------------------
# CacheStats dataclass smoke test
# ---------------------------------------------------------------------------


def test_cache_stats_is_a_slotted_dataclass() -> None:
    stats = CacheStats(
        asset_class=AssetClass.EQUITY,
        last_refresh_ts=None,
        n_symbols=0,
        rows_per_symbol_min=0,
        rows_per_symbol_max=0,
        is_stale=True,
    )
    # ``slots=True`` means we cannot set arbitrary attributes.
    with pytest.raises(AttributeError):
        stats.unexpected = 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Misc: ensure injected clock callable type is honoured
# ---------------------------------------------------------------------------


def test_custom_clock_is_used_for_window_computation() -> None:
    pinned = datetime(2024, 1, 15, 9, 30, tzinfo=UTC)

    def fixed_clock() -> datetime:
        return pinned

    loader = _RecordingLoader(bars_per_symbol={"SPY": _make_df(3)})
    cache = BarsCache(
        equity_loader=loader,
        equity_lookback_days=10,
        clock=fixed_clock,
    )
    cache.refresh(AssetClass.EQUITY, ("SPY",))
    _, start, end = loader.calls[0]
    # The cache normalizes the clock's datetime to a date before calling the
    # loader (loader API requires `date`); pin the converted form.
    assert end == pinned.date()
    assert start == pinned.date() - timedelta(days=10)


def test_clock_accepts_arbitrary_callable() -> None:
    """The clock signature is ``Callable[[], datetime]``; verify acceptance."""
    counter = {"n": 0}
    base = datetime(2024, 1, 1, tzinfo=UTC)

    def stepping_clock() -> datetime:
        counter["n"] += 1
        return base + timedelta(seconds=counter["n"])

    cache = BarsCache(clock=stepping_clock)
    # Just smoke-test that constructing + querying never blows up.
    assert isinstance(cache.is_stale_for(AssetClass.EQUITY), bool)


# Type-narrowing assertion: the callable signature is exposed at runtime.
def test_loader_protocols_are_callable_aliases() -> None:
    from src.runtime.bars_cache import CryptoLoader, EquityLoader

    # These are runtime aliases for ``Callable[...]``; just confirm they
    # exist and can be referenced.
    assert EquityLoader is not None
    assert CryptoLoader is not None


# Avoid an unused-import lint warning from ``Callable`` in this module.
_assert_callable: Callable[[], None] = lambda: None  # noqa: E731
