"use client";

import { api } from "@/lib/api";
import { demoAnalytics, demoEquity } from "@/lib/demo";
import { fmtPct, fmtUsd, fmtPctSigned } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";

type Metric = {
  label: string;
  value: string;
  detail: string;
  bar?: { value: number; warn: number; halt: number; reverse?: boolean };
};

/**
 * Combined analytics + promotion gates + cost & performance.
 * Three columns: analytics 2x2 tiles · 6 promo-gate badges 3x2 · compact perf row.
 * Replaces the old 2:1 split (analytics | cost) so we get more density side-by-side.
 */
export function AnalyticsPanel() {
  // v1: synthetic; replace with /api/coherence + /api/backtest/history when populated.
  const a = demoAnalytics();
  const costsQ = useQuery({ queryKey: ["costs-analytics"], queryFn: api.costs });
  const c = costsQ.data;

  // Sharpe / max DD computed locally from demo equity (consistent with PnlHero/EquityChart).
  const equityQ = useQuery({ queryKey: ["equity-analytics"], queryFn: api.equity });
  const live = equityQ.data ?? [];
  const series = live.length === 0 ? demoEquity().map((p) => p.total) : live.map((p) => p.total);
  const maxDd = (() => {
    let peak = -Infinity;
    let dd = 0;
    for (const v of series) {
      peak = Math.max(peak, v);
      if (peak > 0) dd = Math.max(dd, (peak - v) / peak);
    }
    return dd;
  })();

  const metrics: Metric[] = [
    {
      label: "live win rate (30d)",
      value: fmtPct(a.win_rate_30d),
      detail: `${a.n_trades_30d} trades · bt ${fmtPct(a.win_rate_backtest)}`,
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
      label: "expectancy",
      value: fmtUsd(a.expectancy_usd),
      detail: `per trade · hold ${a.avg_hold_days.toFixed(1)}d`,
    },
  ];

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold tracking-wide text-text">analytics · gates · perf</h2>
          <span className="rounded border border-warn/30 bg-warn/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-warn">
            demo
          </span>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
          best · {a.best_strategy} / weak · {a.worst_strategy}
        </span>
      </header>
      <div className="grid grid-cols-1 divide-y divide-border lg:grid-cols-12 lg:divide-x lg:divide-y-0">
        {/* Analytics — 4 metric tiles in a 2x2 */}
        <div className="lg:col-span-5">
          <div className="grid grid-cols-2">
            {metrics.map((m, i) => (
              <MetricTile
                key={m.label}
                m={m}
                /* hairline grid: top border on row 2, left border on col 2 */
                bordered={i % 2 === 1 ? "left" : i >= 2 ? "top" : ""}
              />
            ))}
          </div>
        </div>

        {/* Promotion gates — 6 badges in a 3x2 grid */}
        <div className="lg:col-span-4 px-4 py-3">
          <div className="flex items-baseline justify-between">
            <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
              promotion gate status
            </span>
            <span className="font-mono text-[10px] text-text-dim">
              live → research only
            </span>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1.5">
            <Gate label="n_trades" pass={a.n_trades_30d >= 30} />
            <Gate label="profit_f" pass={a.profit_factor >= 1.2} />
            <Gate label="coherence" pass={a.coherence >= 0.5} />
            <Gate label="sharpe" pass={a.sharpe_30d >= 0.5} />
            <Gate label="win_rate" pass={a.win_rate_30d >= 0.4} />
            <Gate label="hold_t" pass={a.avg_hold_days <= 21} />
          </div>
        </div>

        {/* Cost + Performance compact row */}
        <div className="lg:col-span-3 px-4 py-3">
          <div className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
            cost · perf (today)
          </div>
          <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1.5">
            <PerfStat label="LLM tok"  value={c ? `${((c.llm_input_tokens + c.llm_output_tokens) / 1000).toFixed(1)}k` : "—"} />
            <PerfStat label="API req"  value={c ? c.api_requests.toLocaleString() : "—"} />
            <PerfStat label="USD est" value={c ? `$${c.estimated_usd.toFixed(3)}` : "—"} />
            <PerfStat
              label="sharpe"
              value={a.sharpe_30d.toFixed(2)}
              tone={a.sharpe_30d >= 1 ? "pos" : a.sharpe_30d >= 0.5 ? "neutral" : "neg"}
            />
            <PerfStat
              label="max DD"
              value={`${(maxDd * 100).toFixed(1)}%`}
              tone={maxDd <= 0.05 ? "pos" : maxDd <= 0.1 ? "neutral" : "neg"}
            />
            <PerfStat
              label="ann ret"
              value={fmtPctSigned(
                series.length >= 2
                  ? (series.at(-1)! / (series[0] || 1)) ** (252 / Math.max(1, series.length)) - 1
                  : 0,
                1,
              )}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function MetricTile({ m, bordered }: { m: Metric; bordered: "" | "left" | "top" }) {
  const bordCls =
    bordered === "left" ? "border-l border-border" : bordered === "top" ? "border-t border-border" : "";
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
  tone,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "neutral";
}) {
  const cls = tone === "pos" ? "text-success" : tone === "neg" ? "text-danger" : "text-text";
  return (
    <div className="flex flex-col leading-tight">
      <span className="font-mono text-[9px] uppercase tracking-wider text-text-dim">{label}</span>
      <span className={`font-mono text-[12px] ${cls}`}>{value}</span>
    </div>
  );
}
