"""Observability: structlog config + Prometheus metrics + /metrics endpoint."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_logging_configures_idempotently():
    from src.observability.logging import configure_logging, get_logger

    configure_logging("INFO")
    configure_logging("INFO")  # second call must not raise
    log = get_logger("test")
    log.info("hello", k=1)


def test_logging_emits_json_line(capsys):
    from src.observability.logging import configure_logging, get_logger

    configure_logging("INFO")
    log = get_logger("t")
    log.info("event_name", foo="bar", n=2)
    captured = capsys.readouterr().out
    last = captured.strip().splitlines()[-1]
    parsed = json.loads(last)
    assert parsed["event"] == "event_name"
    assert parsed["foo"] == "bar"
    assert parsed["n"] == 2
    assert parsed["level"] == "info"


def test_metrics_module_exposes_counters():
    from src.observability.metrics import (
        GATE_DECISIONS,
        JOURNAL_WRITES,
        KILL_INVOCATIONS,
        ORDERS_SUBMITTED,
    )

    # Smoke: all are callable counter/histogram instances.
    ORDERS_SUBMITTED.labels(side="buy", type="market", dry_run="true").inc()
    JOURNAL_WRITES.labels(event="submit").inc()
    GATE_DECISIONS.labels(gate="risk", decision="APPROVE").inc()
    KILL_INVOCATIONS.inc()


@pytest.fixture
def client(monkeypatch, tmp_path: Path):
    from dashboard.api import journal_reader, kill
    from dashboard.api import state as state_module

    journal = tmp_path / "journal"
    journal.mkdir()
    incidents = tmp_path / "live" / "incidents"
    incidents.mkdir(parents=True)
    monkeypatch.setattr(journal_reader, "JOURNAL_DIR", journal)
    monkeypatch.setattr(kill, "INCIDENTS_DIR", incidents)
    monkeypatch.setattr(state_module, "_state", None)
    from dashboard.api.main import app

    return TestClient(app)


def test_metrics_endpoint_is_prometheus_text(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    # At least one of the declared metric families must appear in the scrape body.
    assert re.search(r"^# HELP algo_orders_submitted_total", body, re.MULTILINE)


def test_costs_add_bumps_counter(client):
    before = client.get("/api/costs").json()
    r = client.post(
        "/api/costs/add",
        json={"input_tokens": 100, "output_tokens": 50, "requests": 1, "usd": 0.012},
    )
    assert r.status_code == 200
    after = r.json()
    assert after["llm_input_tokens"] == before["llm_input_tokens"] + 100
    assert after["llm_output_tokens"] == before["llm_output_tokens"] + 50
    assert after["api_requests"] == before["api_requests"] + 1
    assert abs(after["estimated_usd"] - (before["estimated_usd"] + 0.012)) < 1e-6


def test_costs_add_rejects_negative(client):
    r = client.post("/api/costs/add", json={"input_tokens": -1})
    assert r.status_code == 422
