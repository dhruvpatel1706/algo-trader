"""Universe loader: a single source of truth for ticker lists.

Replaces hardcoded tuples scattered across strategies and the CLI. Reads
`docs/universes.yaml` once per process (cached) and exposes named lookups.

Add new universes by editing `docs/universes.yaml`, not by hand-editing strategy
files. Per-strategy assignment lives in the `strategy_universes:` block of the
yaml so a strategy file does not have to be touched to test it on a new asset
list.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

import yaml

from src.config import PROJECT_ROOT

_DEFAULT_UNIVERSE_YAML = PROJECT_ROOT / "docs" / "universes.yaml"
_DEFAULT_STRATEGY_FALLBACK = "spy_qqq"


class UniverseError(LookupError):
    """Raised when a universe key is missing or malformed."""


@lru_cache(maxsize=1)
def _load_yaml(path: str = str(_DEFAULT_UNIVERSE_YAML)) -> dict:
    p = Path(path)
    if not p.exists():
        raise UniverseError(f"universes.yaml not found at {p}")
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise UniverseError(
            f"universes.yaml must be a mapping at top level, got {type(data).__name__}"
        )
    return data


def _to_tuple(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        raise UniverseError(f"universe key {key!r} is empty")
    if isinstance(value, str):
        # Single-symbol convenience.
        return (value,)
    if not isinstance(value, Iterable):
        raise UniverseError(f"universe key {key!r} must be a list, got {type(value).__name__}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise UniverseError(f"universe key {key!r} contains non-string entry {item!r}")
        out.append(item.strip().upper())
    if not out:
        raise UniverseError(f"universe key {key!r} resolved to empty list")
    return tuple(out)


class Universe:
    """Public façade for ticker-list lookups. All methods are classmethods."""

    @classmethod
    def named(cls, name: str) -> tuple[str, ...]:
        """Return the ticker tuple for a named universe.

        Raises UniverseError if the key is missing.
        """
        data = _load_yaml()
        if name not in data:
            raise UniverseError(
                f"unknown universe {name!r}. "
                f"Add it to docs/universes.yaml. Known top-level keys: "
                f"{sorted(k for k in data if not k.startswith('_'))}"
            )
        return _to_tuple(data[name], name)

    @classmethod
    def for_strategy(cls, strategy_name: str) -> tuple[str, ...]:
        """Resolve the universe assigned to a strategy.

        Reads `strategy_universes:` block in the yaml. Falls back to `spy_qqq`
        when a strategy is not registered, so adding a new strategy never
        crashes the bot before its yaml entry is added.
        """
        data = _load_yaml()
        assignments = data.get("strategy_universes") or {}
        if not isinstance(assignments, dict):
            raise UniverseError("strategy_universes must be a mapping")
        target_key = assignments.get(strategy_name, _DEFAULT_STRATEGY_FALLBACK)
        return cls.named(target_key)

    @classmethod
    def is_index_etf(cls, symbol: str) -> bool:
        """Replaces the scattered `{"SPY","QQQ","IWM","DIA"}` literals."""
        data = _load_yaml()
        members = data.get("index_etfs") or []
        return symbol.upper() in {s.upper() for s in members}

    @classmethod
    def sector(cls, symbol: str) -> str | None:
        """Return the sector tag for a symbol, or None if unknown."""
        data = _load_yaml()
        sector_map = data.get("sector_map") or {}
        if not isinstance(sector_map, dict):
            return None
        return sector_map.get(symbol.upper())

    @classmethod
    def known_keys(cls) -> tuple[str, ...]:
        """All top-level keys (universe names) defined in the yaml. Useful for tests."""
        return tuple(sorted(k for k in _load_yaml() if not k.startswith("_")))

    @classmethod
    def reload(cls) -> None:
        """Force reload of the yaml. Tests call this after writing a fixture file."""
        _load_yaml.cache_clear()
