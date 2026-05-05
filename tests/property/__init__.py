"""Property-based tests using hypothesis.

These tests generate adversarial inputs and verify hard invariants. They are
slower than unit tests by design — run with ``pytest -m property`` for nightly
sweeps and ``pytest -m "not property"`` in fast feedback loops.
"""
