"""Prometheus metrics. Counters/histograms stay in-process; scrape /metrics on the dashboard."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

ORDERS_SUBMITTED = Counter(
    "algo_orders_submitted_total",
    "Total orders submitted via PaperBroker.",
    ["side", "type", "dry_run"],
    registry=REGISTRY,
)

JOURNAL_WRITES = Counter(
    "algo_journal_writes_total",
    "Total JSONL records appended to the journal.",
    ["event"],
    registry=REGISTRY,
)

GATE_DECISIONS = Counter(
    "algo_gate_decisions_total",
    "Risk and compliance gate decisions.",
    ["gate", "decision"],
    registry=REGISTRY,
)

KILL_INVOCATIONS = Counter(
    "algo_kill_invocations_total",
    "Manual kill-switch activations.",
    registry=REGISTRY,
)

DASHBOARD_HTTP = Histogram(
    "algo_dashboard_http_seconds",
    "Dashboard HTTP request latency, seconds.",
    ["route"],
    registry=REGISTRY,
)

PORTFOLIO_EQUITY = Gauge(
    "algo_portfolio_equity_usd",
    "Last known portfolio equity ($).",
    registry=REGISTRY,
)

COST_USD_TOTAL = Gauge(
    "algo_llm_cost_usd_total",
    "Cumulative estimated LLM spend ($) since process start.",
    registry=REGISTRY,
)
