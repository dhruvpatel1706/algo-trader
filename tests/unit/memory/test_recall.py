"""Unit tests for :func:`recall_similar` and :func:`cosine`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.memory.embeddings import DeterministicHashProvider
from src.memory.models import TradeMemory
from src.memory.recall import DimensionMismatchError, cosine, recall_similar
from src.memory.store import MemoryStore


def _mem(
    trade_id: str,
    narrative: str,
    *,
    strategy: str = "failed_breakout",
    ts: datetime | None = None,
    embedding: list[float] | None = None,
) -> TradeMemory:
    if ts is None:
        ts = datetime.now(UTC)
    if embedding is None:
        embedding = DeterministicHashProvider().embed(narrative)
    return TradeMemory(
        trade_id=trade_id,
        ts=ts,
        symbol="SPY",
        side="buy",
        strategy=strategy,
        narrative=narrative,
        outcome_pnl_usd=None,
        outcome_r=None,
        outcome_label="open",
        embedding=embedding,
    )


# ---------------------------------------------------------------------------
# cosine helper
# ---------------------------------------------------------------------------


def test_cosine_unit_vectors_returns_dot_product():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine(a, b) == pytest.approx(0.0)
    assert cosine(a, a) == pytest.approx(1.0)


def test_cosine_known_values():
    a = [3.0, 4.0]   # norm 5
    b = [4.0, 3.0]   # norm 5
    # dot = 24, denom = 25 -> 0.96
    assert cosine(a, b) == pytest.approx(0.96)


def test_cosine_dimension_mismatch_raises():
    with pytest.raises(DimensionMismatchError):
        cosine([1.0, 2.0], [1.0, 2.0, 3.0])


def test_cosine_zero_vector_returns_zero():
    assert cosine([0.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# recall_similar
# ---------------------------------------------------------------------------


def test_recall_self_similarity_is_top_and_one(tmp_path):
    provider = DeterministicHashProvider()
    db = MemoryStore(tmp_path / "memory.db")

    db.add(_mem("a", "rejection wick at premarket high"))
    db.add(_mem("b", "VIX spike with bond divergence"))
    db.add(_mem("c", "earnings miss with negative guidance"))

    out = recall_similar(
        "rejection wick at premarket high",
        store=db,
        provider=provider,
        k=3,
        since_days=None,
        # Cosine over the deterministic hash space can be negative for unrelated
        # narratives; relax the floor so all three candidates are returned.
        min_similarity=-1.0,
    )
    assert len(out) == 3
    top_mem, top_sim = out[0]
    assert top_mem.trade_id == "a"
    # Self-similarity for L2-normalized vectors is essentially 1.0.
    assert top_sim == pytest.approx(1.0, abs=1e-5)
    db.close()


def test_recall_top_k_limits_returned_count(tmp_path):
    provider = DeterministicHashProvider()
    db = MemoryStore(tmp_path / "memory.db")
    for i in range(10):
        db.add(_mem(f"t-{i}", f"narrative number {i}"))
    out = recall_similar(
        "narrative number 0",
        store=db,
        provider=provider,
        k=4,
        since_days=None,
    )
    assert len(out) == 4
    # Strictly descending similarity.
    sims = [sim for _, sim in out]
    assert sims == sorted(sims, reverse=True)
    db.close()


def test_recall_min_similarity_filters_out_noise(tmp_path):
    provider = DeterministicHashProvider()
    db = MemoryStore(tmp_path / "memory.db")
    db.add(_mem("a", "alpha alpha alpha"))
    out = recall_similar(
        "alpha alpha alpha",
        store=db,
        provider=provider,
        k=5,
        since_days=None,
        min_similarity=0.99,
    )
    # Self-similarity is 1.0 -> passes the threshold.
    assert len(out) == 1
    out_strict = recall_similar(
        "completely different content",
        store=db,
        provider=provider,
        k=5,
        since_days=None,
        min_similarity=0.99,
    )
    # No match at this threshold for unrelated content.
    assert out_strict == []
    db.close()


def test_recall_strategy_filter_isolates_strategy(tmp_path):
    provider = DeterministicHashProvider()
    db = MemoryStore(tmp_path / "memory.db")
    db.add(_mem("a", "shared narrative", strategy="failed_breakout"))
    db.add(_mem("b", "shared narrative", strategy="mean_reversion"))

    out = recall_similar(
        "shared narrative",
        store=db,
        provider=provider,
        k=5,
        since_days=None,
        strategy_filter="mean_reversion",
    )
    assert len(out) == 1
    assert out[0][0].trade_id == "b"
    db.close()


def test_recall_since_days_excludes_old_rows(tmp_path):
    provider = DeterministicHashProvider()
    db = MemoryStore(tmp_path / "memory.db")
    now = datetime.now(UTC)
    db.add(_mem("old", "the same narrative", ts=now - timedelta(days=400)))
    db.add(_mem("new", "the same narrative", ts=now - timedelta(days=2)))

    out = recall_similar(
        "the same narrative",
        store=db,
        provider=provider,
        k=5,
        since_days=30,
    )
    assert [m.trade_id for m, _ in out] == ["new"]
    db.close()


def test_recall_since_days_and_strategy_filter_compose(tmp_path):
    provider = DeterministicHashProvider()
    db = MemoryStore(tmp_path / "memory.db")
    now = datetime.now(UTC)
    db.add(
        _mem("old_fb", "shared", strategy="failed_breakout", ts=now - timedelta(days=400))
    )
    db.add(
        _mem("new_fb", "shared", strategy="failed_breakout", ts=now - timedelta(days=5))
    )
    db.add(
        _mem("new_mr", "shared", strategy="mean_reversion", ts=now - timedelta(days=5))
    )

    out = recall_similar(
        "shared",
        store=db,
        provider=provider,
        k=5,
        since_days=30,
        strategy_filter="failed_breakout",
    )
    assert [m.trade_id for m, _ in out] == ["new_fb"]
    db.close()


def test_recall_dimension_mismatch_between_query_and_stored_raises(tmp_path):
    provider = DeterministicHashProvider()  # dim 64
    db = MemoryStore(tmp_path / "memory.db")
    # Manually craft a row with a wrong-dim embedding.
    bad = _mem("bad", "x", embedding=[0.1, 0.2, 0.3])
    db.add(bad)
    with pytest.raises(DimensionMismatchError):
        recall_similar(
            "x",
            store=db,
            provider=provider,
            k=5,
            since_days=None,
        )
    db.close()


def test_recall_empty_store_returns_empty_list(tmp_path):
    provider = DeterministicHashProvider()
    db = MemoryStore(tmp_path / "memory.db")
    out = recall_similar(
        "anything",
        store=db,
        provider=provider,
        k=5,
        since_days=None,
    )
    assert out == []
    db.close()


def test_recall_top_k_ordering_is_stable(tmp_path):
    """Two queries with the same narrative must return the same ordering."""
    provider = DeterministicHashProvider()
    db = MemoryStore(tmp_path / "memory.db")
    for i in range(8):
        db.add(_mem(f"t-{i}", f"variant {i} of the breakout setup"))
    a = recall_similar(
        "variant 3 of the breakout setup",
        store=db,
        provider=provider,
        k=5,
        since_days=None,
    )
    b = recall_similar(
        "variant 3 of the breakout setup",
        store=db,
        provider=provider,
        k=5,
        since_days=None,
    )
    assert [m.trade_id for m, _ in a] == [m.trade_id for m, _ in b]
    db.close()
