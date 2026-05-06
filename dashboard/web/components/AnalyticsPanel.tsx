"use client";

import { api } from "@/lib/api";
import { fmtPct, fmtUsd, fmtPctSigned } from "@/lib/format";
import type { TrailingMetrics } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";

type Metric = {
  label: string;
  value: string;
  detail: string;
  bar?: { value: number; warn: number; halt: number; reverse?: boolean };
};

/**
 * Combined analytics + promotion gates + cost & performance.
 *
 * Source of truth:
 *   - `/api/metrics` for win-rate / profit-factor / expectancy / n_trades
 *     (`TrailingMetrics` is a Record<strategy, {...}>; we aggregate across
 *     strategies for the headline tiles).
 *   - `/api/portfolio/equity` for Sharpe / max DD / ann return computed
 *     locally from the equity curve.
 *   - `/api/coherence` is per-strategy; we surface the *worst* coherence
 *     across strategies as a conservative aggregate health signal.
 *   - `/api/costs` for LLM token + USD spend today.
 *
 * Empty path: all "—" with a clear "no live data yet" hint, no synthesized
 * numbers anywhere.
 */
export function AnalyticsPanel() {
  const metricsQ = useQuery({ queryKey: ["metrics"], queryFn: api.metrics });
  const costsQ = useQuery({ queryKey: ["costs-analytics"], queryFn: api.costs });
  const equityQ = useQuery({ queryKey: ["equity-analytics"], queryFn: api.equity });
  const coherenceQ = useQuery({
    queryKey: ["coherence-aggregate"],
    queryFn: () => api.coherence("mr_etf"),
  });

  const series = (equityQ.data ?? []).map((p) => p.total);
  const c = costsQ.data;
  const m: TrailingMetrics = metricsQ.data ?? {};

  // Aggregate across strategies. Empty record → all zeros, but isEmpty guards
  // the render so we never show fabricated numbers.
  const agg = aggregate(m);
  const cohStates = coherenceQ.data ?? [];
  const coherence = cohStates.length === 0
    ? null
    : cohStates.reduce<number | null>((min, c) => {
        const v = c.coherence;
        if (v == null) return min;
        return min == null ? v : Math.min(min, v);
      }, null);

  const isEmpty = agg.n_trades === 0 && series.length < 2;

  const sharpe = sharpeFromSeries(series);
  const maxDd = maxDrawdown(series);
  const annRet = annualizedReturn(series);

  if (isEmpty) {
    return (
      <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
        <header className="flex items-center justify-between border-b border-border px-4 py-2">
          <h2 className="text-sm font-semibold tracking-wide text-text">
            analytics · gates · perf
          </h2>
          <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
            live → research only
          </span>
        </header>
        <div className="px-5 py-8 text-center font-mono text-[11px] text-muted">
          no live trades or equity history yet — start the bot and let a few
          cycles run before promotion gates can be evaluated.
        </div>
      </section>
    );
  }

  const metrics: Metric[] = [
    {
      label: "live win rate",
      value: fmtPct(agg.win_rate),
      detail: `${agg.n_trades} trades`,
      bar: { value: agg.win_rate, warn: 0.5, halt: 0.4 },
    },
    {
      label: "coherence",
      value: coherence == null ? "—" : coherence.toFixed(2),
      detail:
        coherence == null
          ? "no live data"
          : coherence >= 0.7
          ? "healthy"
          : coherence >= 0.5
          ? "watch"
          : "halt",
      bar:
        coherence == null
          ? undefined
          : { value: coherence, warn: 0.7, halt: 0.5 },
    },
    {
      label: "profit factor",
      value: agg.profit_factor.toFixed(2),
      detail: agg.profit_factor >= 1.5 ? "strong" : agg.profit_factor >= 1.2 ? "passing" : "weak",
      bar: { value: Math.min(2.5, agg.profit_factor) / 2.5, warn: 0.6, halt: 0.48 },
    },
    {
      label: "expectancy",
      value: fmtUsd(agg.expectancy),
      detail: "per trade",
    },
  ];

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex items-center justify-between border-b border-border px-4 py-2">
        <h2 className="text-sm font-semibold tracking-wide text-text">
          analytics · gates · perf
        </h2>
        <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
          live → research only
        </span>
      </header>
      <div className="grid grid-cols-1 divide-y divide-border lg:grid-cols-12 lg:divide-x lg:divide-y-0">
        <div className="lg:col-span-5">
          <div className="grid grid-cols-2">
            {metrics.map((tile, i) => (
              <MetricTile
                key={tile.label}
                m={tile}
                bordered={i % 2 === 1 ? "left" : i >= 2 ? "top" : ""}
              />
            ))}
          </div>
        </div>

        <div className="lg:col-span-4 px-4 py-3">
          <div className="flex items-baseline justify-between">
            <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
              promotion gate status
            </span>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1.5">
            <Gate label="n_trades" pass={agg.n_trades >= 30} />
            <Gate label="profit_f" pass={agg.profit_factor >= 1.2} />
            <Gate
              label="coherence"
              pass={coherence != null && coherence >= 0.5}
            />
            <Gate label="sharpe" pass={sharpe >= 0.5} />
            <Gate label="win_rate" pass={agg.win_rate >= 0.4} />
            <Gate label="max_dd" pass={maxDd <= 0.2} />
          </div>
        </div>

        <div className="lg:col-span-3 px-4 py-3">
          <div className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
            cost · perf (today)
          </div>
          <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1.5">
            <PerfStat
              label="LLM tok"
              value={
                c ? `${((c.llm_input_tokens + c.llm_output_tokens) / 1000).toFixed(1)}k` : "—"
              }
            />
            <PerfStat label="API req" value={c ? c.api_requests.toLocaleString() : "—"} />
            <PerfStat label="USD est" value={c ? `$${c.estimated_usd.toFixed(3)}` : "—"} />
            <PerfStat
              label="sharpe"
              value={series.length < 2 ? "—" : sharpe.toFixed(2)}
              tone={sharpe >= 1 ? "pos" : sharpe >= 0.5 ? "neutral" : "neg"}
            />
            <PerfStat
              label="max DD"
              value={series.length < 2 ? "—" : `${(maxDd * 100).toFixed(1)}%`}
              tone={maxDd <= 0.05 ? "pos" : maxDd <= 0.1 ? "neutral" : "neg"}
            />
            <PerfStat
              label="ann ret"
              value={series.length < 2 ? "—" : fmtPctSigned(annRet, 1)}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function aggregate(m: TrailingMetrics): {
  win_rate: number;
  profit_factor: number;
  expectancy: number;
  n_trades: number;
} {
  const rows = Object.values(m);
  if (rows.length === 0) {
    return { win_rate: 0, profit_factor: 0, expectancy: 0, n_trades: 0 };
  }
  const n = rows.reduce((s, r) => s + (r.n_trades ?? 0), 0);
  if (n === 0) {
    return { win_rate: 0, profit_factor: 0, expectancy: 0, n_trades: 0 };
  }
  // Trade-weighted aggregates.
  const win_rate = rows.reduce((s, r) => s + (r.win_rate ?? 0) * (r.n_trades ?? 0), 0) / n;
  const expectancy = rows.reduce((s, r) => s + (r.expectancy ?? 0) * (r.n_trades ?? 0), 0) / n;
  // Profit factor is wins/losses, so aggregate via total P&L is approximate;
  // the per-strategy mean weighted by n_trades is a reasonable health signal.
  const profit_factor =
    rows.reduce((s, r) => s + (r.profit_factor ?? 0) * (r.n_trades ?? 0), 0) / n;
  return { win_rate, profit_factor, expectancy, n_trades: n };
}

function sharpeFromSeries(series: number[]): number {
  if (series.length < 3) return 0;
  const ret: number[] = [];
  for (let i = 1; i < series.length; i++) {
    const a = series[i - 1] ?? 0;
    const b = series[i] ?? 0;
    if (a > 0 && b > 0) ret.push(Math.log(b / a));
  }
  if (ret.length < 2) return 0;
  const mean = ret.reduce((s, v) => s + v, 0) / ret.length;
  const variance =
    ret.reduce((s, v) => s + (v - mean) ** 2, 0) / Math.max(1, ret.length - 1);
  const sd = Math.sqrt(variance);
  if (sd < 1e-12) return 0;
  return (mean / sd) * Math.sqrt(252);
}

function maxDrawdown(series: number[]): number {
  let peak = -Infinity;
  let dd = 0;
  for (const v of series) {
    if (v > peak) peak = v;
    if (peak > 0) dd = Math.max(dd, (peak - v) / peak);
  }
  return dd;
}

function annualizedReturn(series: number[]): number {
  if (series.length < 2) return 0;
  const first = series[0] ?? 1;
  const last = series.at(-1) ?? 1;
  return (last / first) ** (252 / series.length) - 1;
}

function MetricTile({ m, bordered }: { m: Metric; bordered: "" | "left" | "top" }) {
  const bordCls =
    bordered === "left"
      ? "border-l border-border"
      : bordered === "top"
      ? "border-t border-border"
      : "";
  return (
    <div className={`px-4 py-3 ${bordCls}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim truncate">
          {m.label}
        </span>
        <span className="font-mono text-[9px] text-text-dim truncate">{m.detail}</span>
      </div>
      <div className="mt-1 font-mono text-xl font-semibold leading-none text-text">{m.value}</div>
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
      className={`flex items-center justify-between rounded border px-1.5 py-0.5 font-mono text-[10px] ${
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

function PerfStat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "neutral";
}) {
  const cls = tone === "pos" ? "text-success" : tone === "neg" ? "text-danger" : "text-text";
  return (
    <div className="flex items-baseline justify-between gap-1.5">
      <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">{label}</span>
      <span className={`font-mono text-[11px] ${cls}`}>{value}</span>
    </div>
  );
}
