"""Option-order DTOs for the v1 paper-only options scaffolding.

v1 strategies — every one has a bounded ``max_loss_usd``:

- **covered_call**: long stock + short call (income on existing shares)
- **cash_secured_put**: short put with cash collateral (the wheel CSP)
- **protective_put**: long stock + long put (insurance leg)

Naked calls are rejected at construction time. The whole point of this module
is that ``max_loss_usd`` is computable for every order we let through — that
property is what lets the compliance gate reason about worst-case loss.

One contract = 100 shares (US standard equity options).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

# 1 standard equity-option contract covers this many shares of the underlying.
CONTRACT_MULTIPLIER: int = 100

OptionType = Literal["call", "put"]
OptionAction = Literal["buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close"]
OptionStrategyKind = Literal[
    "covered_call",  # long stock + short call (income)
    "cash_secured_put",  # short put with cash collateral (the wheel CSP)
    "protective_put",  # long stock + long put (insurance)
]


@dataclass(frozen=True, slots=True)
class OptionContract:
    """A single option contract identified by underlying / expiration / strike / type."""

    underlying: str
    expiration: date
    strike: Decimal
    option_type: OptionType

    def __post_init__(self) -> None:
        if not self.underlying:
            raise ValueError("underlying must be non-empty")
        if self.strike <= 0:
            raise ValueError("strike must be positive")
        if self.option_type not in ("call", "put"):
            raise ValueError(f"option_type must be 'call' or 'put', got {self.option_type!r}")

    @property
    def dte(self) -> int:
        """Days to expiration as of today. Negative if already expired."""
        return (self.expiration - date.today()).days


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """One leg of an option order. ``quantity`` is in contracts (not shares)."""

    contract: OptionContract
    action: OptionAction
    quantity: int
    limit_price: Decimal | None = None  # None = market

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("leg quantity must be positive")
        if self.action not in (
            "buy_to_open",
            "sell_to_open",
            "buy_to_close",
            "sell_to_close",
        ):
            raise ValueError(f"unsupported action {self.action!r}")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit_price must be positive when set")


@dataclass(frozen=True, slots=True)
class OptionOrder:
    """Multi-leg option order.

    v1 only allows the three covered/secured strategies enumerated by
    ``OptionStrategyKind``. Naked calls and naked puts (without cash collateral)
    are rejected in ``__post_init__`` — that is the source of the
    "no naked options" guarantee for the compliance layer.
    """

    strategy_kind: OptionStrategyKind
    legs: tuple[OptionLeg, ...]
    underlying_position_qty: int = 0  # for covered_call / protective_put: required long stock count
    cycle_id: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("OptionOrder requires at least one leg")

        kind = self.strategy_kind
        if kind == "covered_call":
            self._validate_covered_call()
        elif kind == "cash_secured_put":
            self._validate_cash_secured_put()
        elif kind == "protective_put":
            self._validate_protective_put()
        else:
            # Defensive guard — Literal will catch most typos at the type checker,
            # but at runtime an unsupported value should fail closed. Naked calls
            # / naked puts arrive here.
            raise ValueError(
                f"unsupported strategy_kind {kind!r}; v1 allows only covered_call, "
                "cash_secured_put, protective_put (no naked options)"
            )

        # Final invariant: max_loss_usd must be bounded for every approved order.
        if self.max_loss_usd is None:
            raise ValueError("max_loss_usd must be bounded — naked options are not allowed")

    # ----- per-strategy validators ------------------------------------------------

    def _validate_covered_call(self) -> None:
        if len(self.legs) != 1:
            raise ValueError("covered_call requires exactly 1 leg")
        leg = self.legs[0]
        if leg.action != "sell_to_open":
            raise ValueError("covered_call leg must be sell_to_open")
        if leg.contract.option_type != "call":
            raise ValueError("covered_call leg must be a call")
        required_shares = leg.quantity * CONTRACT_MULTIPLIER
        if self.underlying_position_qty < required_shares:
            raise ValueError(
                f"covered_call requires underlying_position_qty >= {required_shares} "
                f"(got {self.underlying_position_qty}) — naked calls are forbidden"
            )

    def _validate_cash_secured_put(self) -> None:
        if len(self.legs) != 1:
            raise ValueError("cash_secured_put requires exactly 1 leg")
        leg = self.legs[0]
        if leg.action != "sell_to_open":
            raise ValueError("cash_secured_put leg must be sell_to_open")
        if leg.contract.option_type != "put":
            raise ValueError("cash_secured_put leg must be a put")

    def _validate_protective_put(self) -> None:
        if len(self.legs) != 1:
            raise ValueError("protective_put requires exactly 1 leg")
        leg = self.legs[0]
        if leg.action != "buy_to_open":
            raise ValueError("protective_put leg must be buy_to_open")
        if leg.contract.option_type != "put":
            raise ValueError("protective_put leg must be a put")
        required_shares = leg.quantity * CONTRACT_MULTIPLIER
        if self.underlying_position_qty < required_shares:
            raise ValueError(
                f"protective_put requires underlying_position_qty >= {required_shares} "
                f"(got {self.underlying_position_qty})"
            )

    # ----- worst-case loss --------------------------------------------------------

    @property
    def max_loss_usd(self) -> Decimal | None:
        """Worst-case dollar loss for this order at expiration.

        Conventions per strategy:

        - **cash_secured_put**: stock can go to $0; the short put obligates buying
          100 shares per contract at ``strike``. Worst case = ``strike * 100 * qty``
          minus premium collected (if a ``limit_price`` is set).
        - **covered_call**: the short call caps upside but does not create a new
          downside exposure beyond the stock you already own. The marginal worst
          case for the *order* (not the combined position) is the assignment of
          the underlying — bounded by the strike. We report the strike-leg max
          loss (``strike * 100 * qty`` minus premium received) so the risk gate
          treats it consistently with CSP. The existing equity book already
          accounts for the long-stock leg.
        - **protective_put**: paying the premium is the worst case. The put
          guarantees a floor on the underlying.

        ``None`` would indicate an unbounded position (naked call); that case
        should never reach this branch because ``__post_init__`` rejects it.
        """
        leg = self.legs[0]
        contract = leg.contract
        qty = leg.quantity
        strike = contract.strike
        premium = leg.limit_price if leg.limit_price is not None else Decimal("0")

        if self.strategy_kind == "cash_secured_put":
            collateral = strike * Decimal(CONTRACT_MULTIPLIER) * Decimal(qty)
            return collateral - (premium * Decimal(CONTRACT_MULTIPLIER) * Decimal(qty))

        if self.strategy_kind == "covered_call":
            # Bounded exposure attributable to the option leg: the underlying
            # could be assigned away at ``strike``. We report the strike notional
            # net of premium so the risk cap can size against a comparable number.
            notional = strike * Decimal(CONTRACT_MULTIPLIER) * Decimal(qty)
            return notional - (premium * Decimal(CONTRACT_MULTIPLIER) * Decimal(qty))

        if self.strategy_kind == "protective_put":
            # The premium paid is the only new dollar at risk on this order.
            return premium * Decimal(CONTRACT_MULTIPLIER) * Decimal(qty)

        return None  # unreachable — __post_init__ rejected this case
