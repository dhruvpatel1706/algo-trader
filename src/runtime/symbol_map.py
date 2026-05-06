"""Translate strategy-side symbols into the format the broker expects.

Crypto strategies in this codebase use the Binance-style universe
(``BTCUSDT``, ``ETHUSDT``) because the crypto data loader returns that
shape. Alpaca's :class:`alpaca.trading.client.TradingClient` accepts
slash-delimited pairs (``BTC/USD``, ``ETH/USD``). We translate at submit
time so the strategy-side code stays in the universe it generated bars
for.

Equity / gold / silver / bonds: pass-through (already in NYSE format).

Examples:
    >>> map_symbol_for_broker("BTCUSDT", AssetClass.CRYPTO)
    'BTC/USD'
    >>> map_symbol_for_broker("ETHUSDT", AssetClass.CRYPTO)
    'ETH/USD'
    >>> map_symbol_for_broker("SPY", AssetClass.EQUITY)
    'SPY'
"""

from __future__ import annotations

from src.agents.base import AssetClass

__all__ = ["map_symbol_for_broker"]


# Stable-quote suffixes we accept on the strategy side. Order matters for
# greedy matching: ``USDT`` and ``USDC`` must come before ``USD`` so that
# ``BTCUSDT`` doesn't get mis-stripped to ``BTCT``.
_STABLE_SUFFIXES: tuple[str, ...] = ("USDT", "USDC", "BUSD", "USD")


def map_symbol_for_broker(symbol: str, asset_class: AssetClass) -> str:
    """Translate ``symbol`` from strategy form to broker form.

    Args:
        symbol: Symbol as it appears on the strategy side (e.g. ``BTCUSDT``
            for crypto, ``SPY`` for equity).
        asset_class: The agent's asset class. Only ``AssetClass.CRYPTO``
            triggers translation; everything else is pass-through.

    Returns:
        The broker-form symbol (e.g. ``BTC/USD`` for crypto, unchanged
        ticker otherwise).

    Raises:
        ValueError: If ``asset_class`` is :attr:`AssetClass.CRYPTO` but
            ``symbol`` does not end in a recognized stable-quote suffix
            and is not already in slash form.
    """
    if asset_class != AssetClass.CRYPTO:
        return symbol

    # Idempotency: a symbol already in slash form (e.g. "BTC/USD") passes
    # through unchanged so this helper can be safely re-applied.
    if "/" in symbol:
        return symbol

    for suffix in _STABLE_SUFFIXES:
        if symbol.endswith(suffix):
            base = symbol[: -len(suffix)]
            if not base:
                # Pathological case like a bare "USD" — refuse to emit "/USD".
                raise ValueError(f"unrecognized crypto symbol format: {symbol!r}")
            return f"{base}/USD"

    raise ValueError(f"unrecognized crypto symbol format: {symbol!r}")
