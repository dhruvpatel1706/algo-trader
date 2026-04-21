"""Position-sizing math."""

from __future__ import annotations

from decimal import Decimal

import pytest
from src.risk.sizing import (
    drawdown_fraction,
    kelly_fraction,
    portfolio_heat,
    position_size,
    quarter_kelly,
)


def test_position_size_basic():
    qty = position_size(
        equity=Decimal("100000"),
        risk_pct=Decimal("0.01"),
        entry=Decimal("100"),
        stop=Decimal("99"),
    )
    assert qty == 1000


def test_position_size_caps_at_max_position():
    qty = position_size(
        equity=Decimal("100000"),
        risk_pct=Decimal("0.01"),
        entry=Decimal("100"),
        stop=Decimal("99.99"),
        max_position_pct=Decimal("0.10"),
    )
    assert qty == 100


def test_position_size_zero_when_position_cap_blocks():
    qty = position_size(
        equity=Decimal("100"),
        risk_pct=Decimal("0.01"),
        entry=Decimal("500"),
        stop=Decimal("499"),
        max_position_pct=Decimal("0.10"),
    )
    assert qty == 0


def test_position_size_uses_eps_for_tiny_stop():
    qty = position_size(
        equity=Decimal("100000"),
        risk_pct=Decimal("0.01"),
        entry=Decimal("100"),
        stop=Decimal("100"),
    )
    assert qty == 100_000


@pytest.mark.parametrize(
    "kw",
    [
        dict(
            equity=Decimal("0"), risk_pct=Decimal("0.01"), entry=Decimal("100"), stop=Decimal("99")
        ),
        dict(
            equity=Decimal("100000"),
            risk_pct=Decimal("0"),
            entry=Decimal("100"),
            stop=Decimal("99"),
        ),
        dict(
            equity=Decimal("100000"),
            risk_pct=Decimal("1.5"),
            entry=Decimal("100"),
            stop=Decimal("99"),
        ),
        dict(
            equity=Decimal("100000"),
            risk_pct=Decimal("0.01"),
            entry=Decimal("0"),
            stop=Decimal("99"),
        ),
        dict(
            equity=Decimal("100000"),
            risk_pct=Decimal("0.01"),
            entry=Decimal("100"),
            stop=Decimal("0"),
        ),
    ],
)
def test_position_size_rejects_invalid(kw):
    with pytest.raises(ValueError):
        position_size(**kw)


def test_kelly_fraction():
    assert kelly_fraction(0.6, 1.5) == pytest.approx(0.6 - 0.4 / 1.5)


def test_kelly_fraction_clipped_to_zero():
    assert kelly_fraction(0.4, 1.0) == 0.0


def test_quarter_kelly_is_quarter():
    assert quarter_kelly(0.6, 1.5) == pytest.approx(kelly_fraction(0.6, 1.5) / 4)


@pytest.mark.parametrize(
    "wr,wlr",
    [(-0.1, 1.0), (1.1, 1.0), (0.5, 0.0), (0.5, -1.0)],
)
def test_kelly_rejects_out_of_range(wr, wlr):
    with pytest.raises(ValueError):
        kelly_fraction(wr, wlr)


def test_portfolio_heat_sums_open_risk():
    class P:
        def __init__(self, r):
            self.open_risk = Decimal(r)

    heat = portfolio_heat([P("500"), P("750"), P("250")], equity=Decimal("100000"))
    assert heat == Decimal("0.015")


def test_portfolio_heat_zero_for_empty():
    assert portfolio_heat([], equity=Decimal("100000")) == Decimal("0")


def test_portfolio_heat_rejects_zero_equity():
    with pytest.raises(ValueError):
        portfolio_heat([], equity=Decimal("0"))


def test_drawdown_fraction():
    assert drawdown_fraction(Decimal("85"), Decimal("100")) == Decimal("0.15")
    assert drawdown_fraction(Decimal("100"), Decimal("100")) == Decimal("0")
    assert drawdown_fraction(Decimal("110"), Decimal("100")) == Decimal("0")


def test_drawdown_fraction_rejects_zero_peak():
    with pytest.raises(ValueError):
        drawdown_fraction(Decimal("100"), Decimal("0"))
