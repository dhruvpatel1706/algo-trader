"""Tests for ``src.runtime.symbol_map``."""

from __future__ import annotations

import pytest
from src.agents.base import AssetClass
from src.runtime.symbol_map import map_symbol_for_broker

# ---------------------------------------------------------------------------
# Crypto translation.
# ---------------------------------------------------------------------------


def test_btcusdt_crypto_maps_to_btc_usd() -> None:
    assert map_symbol_for_broker("BTCUSDT", AssetClass.CRYPTO) == "BTC/USD"


def test_ethusdt_crypto_maps_to_eth_usd() -> None:
    assert map_symbol_for_broker("ETHUSDT", AssetClass.CRYPTO) == "ETH/USD"


def test_btcusdc_crypto_maps_to_btc_usd() -> None:
    """USDC stable-pair quote also collapses to plain USD on the Alpaca side."""
    assert map_symbol_for_broker("BTCUSDC", AssetClass.CRYPTO) == "BTC/USD"


def test_btcbusd_crypto_maps_to_btc_usd() -> None:
    """BUSD legacy quote — same translation."""
    assert map_symbol_for_broker("BTCBUSD", AssetClass.CRYPTO) == "BTC/USD"


def test_btcusd_crypto_no_suffix_collision_maps_to_btc_usd() -> None:
    """Bare ``USD`` quote also handled (low-volume but valid input)."""
    assert map_symbol_for_broker("BTCUSD", AssetClass.CRYPTO) == "BTC/USD"


def test_already_slashed_symbol_passes_through() -> None:
    """Idempotency: re-applying the helper on broker-form input is safe."""
    assert map_symbol_for_broker("BTC/USD", AssetClass.CRYPTO) == "BTC/USD"
    assert map_symbol_for_broker("ETH/USD", AssetClass.CRYPTO) == "ETH/USD"


def test_unrecognized_crypto_symbol_raises() -> None:
    """Strategy-side bug or new exchange listing should fail loudly, not silently."""
    with pytest.raises(ValueError, match="unrecognized crypto symbol format"):
        map_symbol_for_broker("FOO123", AssetClass.CRYPTO)


def test_bare_stable_quote_raises() -> None:
    """An input that is JUST a stable-quote ticker has no base asset."""
    with pytest.raises(ValueError, match="unrecognized crypto symbol format"):
        map_symbol_for_broker("USDT", AssetClass.CRYPTO)


# ---------------------------------------------------------------------------
# Pass-through paths.
# ---------------------------------------------------------------------------


def test_equity_symbol_passes_through() -> None:
    assert map_symbol_for_broker("SPY", AssetClass.EQUITY) == "SPY"


def test_gold_symbol_passes_through() -> None:
    assert map_symbol_for_broker("GLD", AssetClass.GOLD) == "GLD"


def test_silver_symbol_passes_through() -> None:
    assert map_symbol_for_broker("SLV", AssetClass.SILVER) == "SLV"


def test_bonds_symbol_passes_through() -> None:
    assert map_symbol_for_broker("TLT", AssetClass.BONDS) == "TLT"


def test_governance_symbol_passes_through() -> None:
    """Governance is a meta-class that doesn't trade, but if a symbol ever
    leaks through it should not be mangled."""
    assert map_symbol_for_broker("ANY", AssetClass.GOVERNANCE) == "ANY"
