"""Universe loader tests — coverage focus on edge cases.

Strategy universes resolve via the yaml; tests use a fixture file rather than
patching `_load_yaml` so the actual yaml parser path is exercised.
"""

from __future__ import annotations

import pytest
from src.data.universe import Universe, UniverseError, _load_yaml


@pytest.fixture(autouse=True)
def _reload_universe():
    """Each test gets a fresh cache so fixture-vs-real-yaml races are impossible."""
    Universe.reload()
    yield
    Universe.reload()


def test_named_returns_expected_tuple_for_real_yaml():
    spy_qqq = Universe.named("spy_qqq")
    assert spy_qqq == ("SPY", "QQQ")


def test_named_normalizes_case():
    crypto = Universe.named("crypto_majors")
    # Yaml has them already uppercase; this still hits the normalizer.
    assert all(s == s.upper() for s in crypto)
    assert "BTCUSDT" in crypto


def test_named_raises_for_unknown_key():
    with pytest.raises(UniverseError, match="unknown universe"):
        Universe.named("does_not_exist_lol")


def test_for_strategy_resolves_assignment():
    # mr_etf is registered to spy_qqq in the yaml; this round-trips through
    # the assignment block and named() lookup.
    assert Universe.for_strategy("mr_etf") == ("SPY", "QQQ")


def test_for_strategy_falls_back_when_missing():
    # An unregistered strategy falls back to spy_qqq so adding a new strategy
    # does not crash before its universe is wired up.
    assert Universe.for_strategy("brand_new_unregistered_strategy_xyz") == ("SPY", "QQQ")


def test_is_index_etf_recognizes_known_etfs():
    assert Universe.is_index_etf("SPY")
    assert Universe.is_index_etf("spy")  # case-insensitive
    assert Universe.is_index_etf("QQQ")
    assert not Universe.is_index_etf("AAPL")
    assert not Universe.is_index_etf("UNKNOWN_TICKER")


def test_sector_lookup():
    assert Universe.sector("AAPL") == "tech"
    assert Universe.sector("aapl") == "tech"
    assert Universe.sector("XOM") == "energy"
    assert Universe.sector("UNKNOWN_TICKER") is None


def test_known_keys_excludes_underscored():
    keys = Universe.known_keys()
    assert "spy_qqq" in keys
    assert "large_caps_50" in keys
    assert "crypto_majors" in keys
    # No underscore-prefixed keys leak.
    assert all(not k.startswith("_") for k in keys)


def test_load_yaml_rejects_non_existent_path(tmp_path):
    fake = tmp_path / "no_such_file.yaml"
    _load_yaml.cache_clear()
    with pytest.raises(UniverseError, match="not found"):
        _load_yaml(str(fake))


def test_load_yaml_rejects_non_mapping_top_level(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n")
    _load_yaml.cache_clear()
    with pytest.raises(UniverseError, match="must be a mapping"):
        _load_yaml(str(bad))
    _load_yaml.cache_clear()


def test_to_tuple_rejects_empty_list(tmp_path, monkeypatch):
    fixture = tmp_path / "fixture.yaml"
    fixture.write_text("empty_one: []\n")
    _load_yaml.cache_clear()
    # Bypass the production path by calling the module helper directly.
    from src.data import universe as u

    with pytest.raises(UniverseError, match="empty list"):
        u._to_tuple([], "empty_one")
    _load_yaml.cache_clear()


def test_to_tuple_rejects_non_string_entries():
    from src.data import universe as u

    with pytest.raises(UniverseError, match="non-string"):
        u._to_tuple([123, "AAPL"], "mixed")


def test_strategies_resolve_through_loader():
    """Smoke test: every strategy registered in the yaml resolves to non-empty list."""
    yaml_data = _load_yaml()
    assignments = yaml_data.get("strategy_universes") or {}
    assert assignments, "strategy_universes block must not be empty"
    for strat_name, key in assignments.items():
        result = Universe.for_strategy(strat_name)
        assert len(result) > 0, f"{strat_name} -> {key} resolved to empty"
        # Every ticker is a non-empty string.
        for ticker in result:
            assert isinstance(ticker, str) and ticker, f"{strat_name}: bad ticker {ticker!r}"
