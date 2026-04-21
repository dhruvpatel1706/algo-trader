"""Strategy base + Signal validation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from src.strategies.base import Signal


def _sig(**kw):
    defaults = dict(
        symbol="SPY",
        side="buy",
        entry=Decimal("100"),
        stop=Decimal("99"),
        target=Decimal("103"),
        confidence=0.6,
        strategy_tag="test",
        timestamp=datetime.now(UTC),
    )
    defaults.update(kw)
    return Signal(**defaults)


def test_signal_basic():
    s = _sig()
    assert s.symbol == "SPY"
    assert s.confidence == 0.6


@pytest.mark.parametrize("conf", [-0.1, 1.5])
def test_signal_rejects_bad_confidence(conf):
    with pytest.raises(ValueError):
        _sig(confidence=conf)


def test_signal_rejects_zero_entry_stop():
    with pytest.raises(ValueError):
        _sig(entry=Decimal("0"))
    with pytest.raises(ValueError):
        _sig(stop=Decimal("0"))
