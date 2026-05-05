"""Tests for boot-time journal/broker reconciliation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from src.journal.writer import JournalWriter
from src.runtime.recovery import ReconcileReport, reconcile_on_boot


class _FakeBroker:
    """Bare-minimum broker stub. Exposes ``get_all_positions``."""

    def __init__(self, positions: list[dict[str, Any]]):
        self._positions = positions

    def get_all_positions(self) -> list[dict[str, Any]]:
        return self._positions


def _seed_journal(journal_dir: Path, day: date, events: list[dict[str, Any]]) -> None:
    """Write events to ``journal_dir/{day}.jsonl`` directly."""
    # Construct JournalWriter to ensure mkdir runs even when events is empty;
    # we then write directly so we control the date stamp on the file name.
    _ = JournalWriter(journal_dir)
    path = journal_dir / f"{day.isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev))
            f.write("\n")


def test_empty_journal_empty_broker_is_ok(tmp_path) -> None:
    report = reconcile_on_boot(tmp_path, _FakeBroker([]), today=date(2025, 6, 1))
    assert report.severity == "ok"
    assert report.divergence_count == 0
    assert report.expected == {}
    assert report.actual == {}


def test_buy_fill_matched_by_broker_position(tmp_path) -> None:
    day = date(2025, 6, 2)
    _seed_journal(
        tmp_path,
        day,
        [{"event": "fill", "symbol": "SPY", "side": "buy", "qty": 10}],
    )
    broker = _FakeBroker([{"symbol": "SPY", "qty": 10, "side": "long"}])
    report = reconcile_on_boot(tmp_path, broker, today=day)
    assert report.severity == "ok"
    assert report.divergence_count == 0
    assert report.expected == {"SPY": 10}
    assert report.actual == {"SPY": 10}


def test_buy_fill_with_no_broker_position_halts(tmp_path) -> None:
    day = date(2025, 6, 3)
    _seed_journal(
        tmp_path,
        day,
        [{"event": "fill", "symbol": "SPY", "side": "buy", "qty": 5}],
    )
    broker = _FakeBroker([])
    report = reconcile_on_boot(tmp_path, broker, today=day)
    assert report.severity == "halt"
    assert report.divergence_count == 1
    assert report.expected == {"SPY": 5}
    assert report.actual == {}
    assert "SPY" in report.notes


def test_broker_has_position_journal_does_not(tmp_path) -> None:
    day = date(2025, 6, 4)
    # No journal file at all.
    broker = _FakeBroker([{"symbol": "GLD", "qty": 3, "side": "long"}])
    report = reconcile_on_boot(tmp_path, broker, today=day)
    assert report.severity == "halt"
    assert report.divergence_count == 1


def test_buy_then_exit_zeros_position(tmp_path) -> None:
    day = date(2025, 6, 5)
    _seed_journal(
        tmp_path,
        day,
        [
            {"event": "fill", "symbol": "SPY", "side": "buy", "qty": 7},
            {"event": "exit", "symbol": "SPY"},
        ],
    )
    broker = _FakeBroker([])
    report = reconcile_on_boot(tmp_path, broker, today=day)
    assert report.severity == "ok"
    assert report.expected == {}


def test_short_position_uses_signed_qty(tmp_path) -> None:
    day = date(2025, 6, 6)
    _seed_journal(
        tmp_path,
        day,
        [{"event": "fill", "symbol": "TLT", "side": "sell", "qty": 4}],
    )
    broker = _FakeBroker([{"symbol": "TLT", "qty": 4, "side": "short"}])
    report = reconcile_on_boot(tmp_path, broker, today=day)
    assert report.severity == "ok"
    assert report.expected == {"TLT": -4}
    assert report.actual == {"TLT": -4}


def test_to_dict_roundtrips() -> None:
    report = ReconcileReport(
        expected={"SPY": 10},
        actual={"SPY": 10},
        divergence_count=0,
        severity="ok",
        notes="ok",
    )
    d = report.to_dict()
    assert d["expected"] == {"SPY": 10}
    assert d["actual"] == {"SPY": 10}
    assert d["divergence_count"] == 0
    assert d["severity"] == "ok"
    assert d["notes"] == "ok"


def test_malformed_journal_line_is_skipped(tmp_path) -> None:
    day = date(2025, 6, 7)
    path = tmp_path / f"{day.isoformat()}.jsonl"
    path.write_text('{"event": "fill", "symbol": "SPY", "side": "buy", "qty": 1}\n{not-json}\n')
    broker = _FakeBroker([{"symbol": "SPY", "qty": 1, "side": "long"}])
    report = reconcile_on_boot(tmp_path, broker, today=day)
    assert report.severity == "ok"


def test_broker_get_positions_dict_shape(tmp_path) -> None:
    """Recovery should also accept ``get_positions()`` returning a list[dict]."""
    day = date(2025, 6, 8)
    _seed_journal(
        tmp_path,
        day,
        [{"event": "fill", "symbol": "BTC/USD", "side": "buy", "qty": 1}],
    )

    class _Broker:
        def get_positions(self) -> list[dict[str, Any]]:
            return [{"symbol": "BTC/USD", "qty": 1, "side": "long"}]

    report = reconcile_on_boot(tmp_path, _Broker(), today=day)
    assert report.severity == "ok"


def test_broker_with_no_position_methods_treated_as_empty(tmp_path) -> None:
    day = date(2025, 6, 9)
    # Empty journal, broker with no relevant methods => still ok.
    report = reconcile_on_boot(tmp_path, object(), today=day)
    assert report.severity == "ok"
    assert report.actual == {}


def test_broker_method_raising_does_not_crash(tmp_path) -> None:
    day = date(2025, 6, 10)

    class _AngryBroker:
        def get_all_positions(self):
            raise RuntimeError("network down")

    report = reconcile_on_boot(tmp_path, _AngryBroker(), today=day)
    # Empty journal + (defensive) empty actual -> ok. No crash.
    assert report.severity == "ok"


@pytest.mark.parametrize(
    "event",
    [
        {"event": "fill"},  # missing symbol
        {"event": "fill", "symbol": "SPY"},  # missing qty
        {"event": "fill", "symbol": "SPY", "side": "buy", "qty": "garbage"},
    ],
)
def test_malformed_fill_events_are_skipped(tmp_path, event) -> None:
    day = date(2025, 6, 11)
    _seed_journal(tmp_path, day, [event])
    report = reconcile_on_boot(tmp_path, _FakeBroker([]), today=day)
    assert report.severity == "ok"
    assert report.expected == {}
