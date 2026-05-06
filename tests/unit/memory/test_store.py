"""Unit tests for :class:`MemoryStore`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.memory.embeddings import DeterministicHashProvider
from src.memory.models import TradeMemory
from src.memory.store import MemoryStore, MemoryStoreError


def _make_memory(
    trade_id: str = "01H1",
    *,
    ts: datetime | None = None,
    symbol: str = "SPY",
    side: str = "buy",
    strategy: str = "failed_breakout",
    narrative: str = "rejection wick at premarket high in low-VIX regime",
    outcome_pnl_usd: float | None = None,
    outcome_r: float | None = None,
    outcome_label: str | None = "open",
    embedding: list[float] | None = None,
) -> TradeMemory:
    if ts is None:
        ts = datetime.now(UTC)
    if embedding is None:
        embedding = DeterministicHashProvider().embed(narrative)
    return TradeMemory(
        trade_id=trade_id,
        ts=ts,
        symbol=symbol,
        side=side,
        strategy=strategy,
        narrative=narrative,
        outcome_pnl_usd=outcome_pnl_usd,
        outcome_r=outcome_r,
        outcome_label=outcome_label,
        embedding=embedding,
    )


def test_add_and_get_round_trip_preserves_all_fields(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    ts = datetime(2025, 3, 12, 14, 30, 0, tzinfo=UTC)
    mem = _make_memory(
        trade_id="t-1",
        ts=ts,
        symbol="QQQ",
        side="sell",
        strategy="mean_reversion",
        narrative="overshoot at -2 sigma Bollinger",
        outcome_pnl_usd=125.5,
        outcome_r=0.7,
        outcome_label="win",
    )
    db.add(mem)

    fetched = db.get("t-1")
    assert fetched is not None
    assert fetched.trade_id == "t-1"
    assert fetched.symbol == "QQQ"
    assert fetched.side == "sell"
    assert fetched.strategy == "mean_reversion"
    assert fetched.narrative == "overshoot at -2 sigma Bollinger"
    assert fetched.outcome_pnl_usd == pytest.approx(125.5)
    assert fetched.outcome_r == pytest.approx(0.7)
    assert fetched.outcome_label == "win"
    assert fetched.ts == ts
    # Embedding is stored as float32 — tolerate small precision loss.
    assert len(fetched.embedding) == len(mem.embedding)
    for stored, original in zip(fetched.embedding, mem.embedding, strict=True):
        assert stored == pytest.approx(original, rel=1e-5, abs=1e-6)
    db.close()


def test_get_returns_none_for_unknown_trade_id(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    assert db.get("nope") is None
    db.close()


def test_count_starts_at_zero_and_increments(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    assert db.count() == 0
    db.add(_make_memory(trade_id="a"))
    db.add(_make_memory(trade_id="b"))
    assert db.count() == 2
    db.close()


def test_duplicate_trade_id_raises_memory_store_error(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    db.add(_make_memory(trade_id="dupe"))
    with pytest.raises(MemoryStoreError):
        db.add(_make_memory(trade_id="dupe"))
    db.close()


def test_add_batch_atomic_on_failure(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    db.add(_make_memory(trade_id="existing"))

    # Second item collides with the existing PK — entire batch must roll back.
    new_batch = [
        _make_memory(trade_id="ok-1"),
        _make_memory(trade_id="existing"),  # PK collision
        _make_memory(trade_id="ok-2"),
    ]
    with pytest.raises(MemoryStoreError):
        db.add_batch(new_batch)

    # The "existing" row is still there, but neither ok-1 nor ok-2 was written.
    assert db.count() == 1
    assert db.get("existing") is not None
    assert db.get("ok-1") is None
    assert db.get("ok-2") is None
    db.close()


def test_add_batch_empty_is_a_noop(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    db.add_batch([])
    assert db.count() == 0
    db.close()


def test_add_batch_writes_all_rows_when_clean(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    db.add_batch(
        [
            _make_memory(trade_id="a"),
            _make_memory(trade_id="b"),
            _make_memory(trade_id="c"),
        ]
    )
    assert db.count() == 3
    db.close()


def test_update_outcome_only_modifies_outcome_columns(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    original = _make_memory(
        trade_id="t-up",
        narrative="opening drive failure",
        outcome_label="open",
    )
    db.add(original)

    db.update_outcome("t-up", pnl_usd=-220.0, r=-1.0, label="loss")
    fetched = db.get("t-up")
    assert fetched is not None
    assert fetched.outcome_pnl_usd == pytest.approx(-220.0)
    assert fetched.outcome_r == pytest.approx(-1.0)
    assert fetched.outcome_label == "loss"
    # Everything else is unchanged.
    assert fetched.symbol == original.symbol
    assert fetched.side == original.side
    assert fetched.strategy == original.strategy
    assert fetched.narrative == original.narrative
    # Embedding bytes preserved (modulo float32 loss).
    for stored, before in zip(fetched.embedding, original.embedding, strict=True):
        assert stored == pytest.approx(before, rel=1e-5, abs=1e-6)
    db.close()


def test_update_outcome_unknown_trade_id_is_noop(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    # Should not raise.
    db.update_outcome("ghost", 1.0, 1.0, "win")
    assert db.count() == 0
    db.close()


def test_all_with_since_filter(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    now = datetime.now(UTC)
    old = _make_memory(trade_id="old", ts=now - timedelta(days=400))
    recent = _make_memory(trade_id="recent", ts=now - timedelta(days=10))
    db.add(old)
    db.add(recent)

    cutoff = now - timedelta(days=30)
    rows = db.all(since=cutoff)
    assert [r.trade_id for r in rows] == ["recent"]

    rows_all = db.all()
    assert {r.trade_id for r in rows_all} == {"old", "recent"}

    rows_capped = db.all(limit=1)
    assert len(rows_capped) == 1
    db.close()


def test_all_orders_newest_first(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    base = datetime.now(UTC)
    # Insert deliberately out of chronological order.
    db.add(_make_memory(trade_id="middle", ts=base - timedelta(days=5)))
    db.add(_make_memory(trade_id="newest", ts=base - timedelta(days=1)))
    db.add(_make_memory(trade_id="oldest", ts=base - timedelta(days=20)))

    rows = db.all()
    assert [r.trade_id for r in rows] == ["newest", "middle", "oldest"]
    db.close()


def test_close_is_idempotent(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    db.close()
    # Should not raise on second close.
    db.close()
