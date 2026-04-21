"""Strategy registry."""

from __future__ import annotations

from importlib import import_module

from src.strategies.base import Signal, Strategy

__all__ = ["Signal", "Strategy", "load_strategy"]


def load_strategy(name: str) -> Strategy:
    """Import `src.strategies.<name>` and instantiate the first Strategy subclass found."""
    module = import_module(f"src.strategies.{name}")
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, Strategy) and value is not Strategy:
            return value()
    raise LookupError(f"no Strategy subclass found in src.strategies.{name}")
