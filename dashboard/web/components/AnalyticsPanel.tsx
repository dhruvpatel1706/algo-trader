"use client";

import { demoAnalytics } from "@/lib/demo";
import { fmtPct, fmtUsd, fmtPctSigned } from "@/lib/format";

type Metric = {
  label: string;
  value: string;
  detail: string;
  bar?: { value: number; warn: number; halt: number; reverse?: boolean };
};

export function AnalyticsPanel() {
  // v1: synthetic; replace with /api/coherence + /api/backtest/history when populated.
  const a = demoAnalytics();

  const metrics: Metric[] = [
    {
      label: "live win rate (30d)",
      value: fmtPct(a.win_rate_30d),
      detail: `${a.n_trades_30d} trades · backtest ${fmtPct(a.win_rate_backtest)}`,
      bar: { value: a.win_rate_30d, warn: 0.5, halt: 0.4 },
    },
    {
      label: "coherence",
      value: a.coherence.toFixed(2),
      detail: a.coherence >= 0.7 ? "healthy" : a.coherence >= 0.5 ? "watch" : "halt",
      bar: { value: a.coherence, warn: 0.7, halt: 0.5 },
    },
    {
      label: "profit factor",
      value: a.profit_factor.toFixed(2),
      detail: a.profit_factor >= 1.5 ? "strong" : a.profit_factor >= 1.2 ? "passing" : "weak",
      bar: { value: Math.min(2.5, a.profit_factor) / 2.5, warn: 0.6, halt: 0.48 },
    },
    {
      label: "sharpe (30d)",
      value: a.sharpe_30d.toFixed(2),
      detail: a.sharpe_30d >= 1.5 ? "top 1%" : a.sharpe_30d >= 1.0 ? "top decile" : "median",
      bar: { value: Math.min(2.5, a.sharpe_30d) / 2.5, warn: 0.6, halt: 0.4 },
    },
    {
      label: "expectancy",
      value: fmtUsd(a.expectancy_usd),
      detail: `per trade · avg hold ${a.avg_hold_days.toFixed(1)}d`,
    },
    {
      label: "best strategy (30d)",
      value: a.best_strategy,
      detail: "highest realized P&L",
    },
    {
      label: "weakest strategy (30d)",
      value: a.worst_strategy,
      detail: "research-only candidate",
    },
  ];

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex items-center justify-between border-b border-border px-5 py-3">
        <h2 className="text-sm font-semibold tracking-wide text-text">analytics</h2>
        <span className="rounded border border-warn/30 bg-warn/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-warn">
          demo
        </span>
      </header>
      <div className="grid grid-cols-1 divide-y divide-border md:grid-cols-2 md:divide-x md:divide-y-0">
        <div className="grid grid-cols-1 divide-y divide-border">
          {metrics.slice(0, 4).map((m) => (
            <MetricRow key={m.label} m={m} />
          ))}
        </div>
        <div className="grid grid-cols-1 divide-y divide-border">
          {metrics.slice(4).map((m) => (
            <MetricRow key={m.label} m={m} />
          ))}
          <div className="px-5 py-4">
            <div className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
              promotion gate status
            </div>
            <div className="mt-1.5 grid grid-cols-3 gap-2 text-[11px]">
              <Gate label="n_trades" pass={a.n_trades_30d >= 30} />
              <Gate label="profit factor" pass={a.profit_factor >= 1.2} />
              <Gate label="coherence" pass={a.coherence >= 0.5} />
              <Gate label="sharpe" pass={a.sharpe_30d >= 0.5} />
              <Gate label="win rate" pass={a.win_rate_30d >= 0.4} />
              <Gate label="hold time" pass={a.avg_hold_days <= 21} />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function MetricRow({ m }: { m: Metric }) {
  return (
    <div className="px-5 py-4">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
          {m.label}
        </span>
        <span className="font-mono text-[10px] text-text-dim">{m.detail}</span>
      </div>
      <div className="mt-1.5 font-mono text-2xl font-semibold text-text">{m.value}</div>
      {m.bar && (
        <div className="relative mt-2 h-1 overflow-hidden rounded-full bg-bg">
          <div
            className={`h-full rounded-full transition-[width] duration-500 ${
              m.bar.value >= m.bar.warn
                ? "bg-success"
                : m.bar.value >= m.bar.halt
                ? "bg-warn"
                : "bg-danger"
            }`}
            style={{ width: `${Math.min(100, Math.max(0, m.bar.value * 100))}%` }}
          />
          <div
            className="absolute top-0 h-full w-px bg-text-dim/40"
            style={{ left: `${m.bar.warn * 100}%` }}
          />
          <div
            className="absolute top-0 h-full w-px bg-danger/60"
            style={{ left: `${m.bar.halt * 100}%` }}
          />
        </div>
      )}
    </div>
  );
}

function Gate({ label, pass }: { label: string; pass: boolean }) {
  return (
    <span
      className={`flex items-center justify-between rounded border px-2 py-1 font-mono text-[10px] ${
        pass
          ? "border-success/30 bg-success/5 text-success"
          : "border-danger/30 bg-danger/5 text-danger"
      }`}
    >
      <span className="uppercase tracking-wider text-text-dim">{label}</span>
      <span>{pass ? "pass" : "fail"}</span>
    </span>
  );
}
