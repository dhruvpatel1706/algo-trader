"""End-to-end check_limits behaviour."""

from __future__ import annotations

from decimal import Decimal

from src.risk.limits import PortfolioSnapshot, ProposedTrade, check_limits


def _snap(equity="100000", realized="0", unrealized="0", peak=None, positions=()):
    return PortfolioSnapshot(
        equity=Decimal(equity),
        cash=Decimal(equity),
        realized_pnl_today=Decimal(realized),
        unrealized_pnl_today=Decimal(unrealized),
        trailing_peak_equity=Decimal(peak) if peak else Decimal(equity),
        open_positions=positions,
    )


def _trade(**kw):
    defaults = dict(
        symbol="SPY",
        side="buy",
        entry=Decimal("100"),
        stop=Decimal("99"),
        strategy_tag="t",
    )
    defaults.update(kw)
    return ProposedTrade(**defaults)


def test_clean_buy_approved():
    # entry $10, stop $9: 1% of $100k = $1000 risk / $1 = 1000 shares.
    # position cap (10% = $10k) / $10 = 1000 shares — both caps align, no violation.
    d = check_limits(_trade(entry=Decimal("10"), stop=Decimal("9")), _snap())
    assert d.approve
    assert d.adjusted_size == 1000


def test_buy_stop_above_entry_rejected():
    d = check_limits(_trade(stop=Decimal("101")), _snap())
    assert not d.approve
    assert "below entry" in d.reason


def test_sell_stop_below_entry_rejected():
    d = check_limits(_trade(side="sell", stop=Decimal("99")), _snap())
    assert not d.approve
    assert "above entry" in d.reason


def test_drawdown_halt_triggers():
    d = check_limits(_trade(), _snap(equity="80000", peak="100000"))
    assert not d.approve
    assert "drawdown" in d.reason.lower()


def test_daily_loss_halt_triggers():
    d = check_limits(_trade(), _snap(realized="-2500"))
    assert not d.approve
    assert "intraday" in d.reason.lower() or "halted" in d.reason.lower()


def test_single_position_cap_enforced():
    d = check_limits(_trade(stop=Decimal("99.99")), _snap())
    assert d.approve
    assert d.adjusted_size == 100


def test_portfolio_heat_cap_enforced():
    class P:
        def __init__(self, r):
            self.open_risk = Decimal(r)

    # Existing 5.5% heat. New trade adds 1% (1000 shares x $1 stop). Total 6.5% > 6% cap.
    # Use entry $10, stop $9 so qty isn't position-capped (which would suppress added risk).
    snap = _snap(positions=(P("5500"),))
    d = check_limits(_trade(entry=Decimal("10"), stop=Decimal("9")), snap)
    assert not d.approve
    assert "heat" in d.reason.lower()


# ---------------------------------------------------------------------------
# Cumulative single-position cap
# ---------------------------------------------------------------------------


def test_existing_position_already_at_cap_rejected():
    """Symbol already holds 10% of equity → fresh add must be refused even
    when the new fill in isolation is small enough to satisfy the cap."""
    # $100k equity, MAX_SINGLE_POSITION 10% = $10k cap. Existing $10k in ETH.
    d = check_limits(
        _trade(symbol="ETH", entry=Decimal("100"), stop=Decimal("99")),
        _snap(),
        existing_notional_in_symbol=Decimal("10000"),
    )
    assert not d.approve
    assert "single-position cap" in d.reason.lower()


def test_existing_position_partial_room_sized_down():
    """Symbol holds 8% of equity ($8k). Cap is 10% = $10k. Only $2k room
    left → at entry $100, qty must be capped to floor(2000/100) = 20, not
    the per-trade-risk-budget answer of 1000."""
    d = check_limits(
        _trade(symbol="ETH", entry=Decimal("100"), stop=Decimal("99")),
        _snap(),
        existing_notional_in_symbol=Decimal("8000"),
    )
    assert d.approve
    assert d.adjusted_size == 20


def test_cumulative_cap_stacks_correctly():
    """Default existing_notional=0 keeps v1 sizing exactly intact —
    regression guard for the existing test_clean_buy_approved expectation."""
    d = check_limits(
        _trade(entry=Decimal("10"), stop=Decimal("9")), _snap()
    )
    assert d.approve
    assert d.adjusted_size == 1000


def test_cumulative_cap_rejects_when_new_pushes_over():
    """Symbol holds 9.5% ($9500). New trade qty (computed by per-trade-risk
    budget) would total $10500 cumulative > $10k cap → rejected with the
    cumulative-notional reason, not the in-isolation reason."""
    d = check_limits(
        _trade(symbol="ETH", entry=Decimal("100"), stop=Decimal("99")),
        _snap(),
        existing_notional_in_symbol=Decimal("9500"),
    )
    # qty gets capped to floor(500/100)=5, so cumulative = 9500+500 = 10000 → equal, allowed.
    assert d.approve
    assert d.adjusted_size == 5
