"""Config validators must fail closed on out-of-bounds risk caps."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError
from src.config import Settings, get_settings


def test_defaults_within_caps():
    s = get_settings()
    assert s.MAX_PER_TRADE_RISK == Decimal("0.01")
    assert s.MAX_PORTFOLIO_HEAT == Decimal("0.06")
    assert s.MAX_SINGLE_POSITION == Decimal("0.10")
    assert s.DAILY_LOSS_HALT == Decimal("-0.02")
    assert s.DRAWDOWN_HALT == Decimal("0.15")
    assert s.ALPACA_PAPER_TRADE is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("MAX_PER_TRADE_RISK", "0.05"),
        ("MAX_PER_TRADE_RISK", "0"),
        ("MAX_PORTFOLIO_HEAT", "0.20"),
        ("MAX_SINGLE_POSITION", "0.50"),
        ("DAILY_LOSS_HALT", "0.01"),
        ("DRAWDOWN_HALT", "0.50"),
    ],
)
def test_out_of_bounds_rejected(monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    with pytest.raises(ValidationError):
        Settings()


def test_live_trading_default_zero():
    assert get_settings().LIVE_TRADING == "0"


def test_paths_resolve(tmp_path):
    s = get_settings()
    assert s.journal_dir.name == "journal"
    assert s.backtests_dir.name == "backtests"
    assert s.live_dir.name == "live"
