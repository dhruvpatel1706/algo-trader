"use client";

import { api } from "@/lib/api";
import { demoEquity } from "@/lib/demo";
import {
  fmtUsd,
  fmtUsdSigned,
  fmtPctSigned,
  pnlColorClass,
  pnlGlowClass,
} from "@/lib/format";
import { useQuery } from "@tanstack/react-query";

type Stat = {
  label: string;
  value: string;
  detail?: string;
  emphasis?: "pos" | "neg" | "neutral";
};

function sparkline(values: number[], width = 96, height = 28): string {
  if (values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * stepX).toFixed(1)},${(height - ((v - min) / span) * height).toFixed(1)}`)
    .join(" ");
  return points;
}

export function PnlHero() {
  const equityQ = useQuery({ queryKey: ["equity-hero"], queryFn: api.equity });
  const live = equityQ.data ?? [];
  const usingDemo = live.length === 0;
  const series = usingDemo ? demoEquity().map((p) => ({ ts: p.ts, total: p.total })) : live;

  const last = series.at(-1)?.total ?? 0;
  const first = series[0]?.total ?? 0;
  const day = (() => {
    if (series.length < 2) return 0;
    const dayAgoTs = Date.now() - 86_400_000;
    let dayAgoIdx = 0;
    for (let i = 0; i < series.length; i++) {
      if (new Date(series[i]!.ts).getTime() <= dayAgoTs) dayAgoIdx = i;
    }
    return last - (series[dayAgoIdx]?.total ?? first);
  })();
  const totalPnl = last - first;
  const totalPct = first > 0 ? totalPnl / first : 0;
  const peak = series.reduce((m, p) => Math.max(m, p.total), -Infinity);
  const dd = peak > 0 ? (peak - last) / peak : 0;
  const sparkPath = sparkline(series.map((p) => p.total));

  const stats: Stat[] = [
    {
      label: "equity",
      value: fmtUsd(last),
      detail: `peak ${fmtUsd(peak, { compact: true })}`,
    },
    {
      label: "day P&L",
      value: fmtUsdSigned(day),
      detail: fmtPctSigned(first > 0 ? day / first : 0),
      emphasis: day > 0 ? "pos" : day < 0 ? "neg" : "neutral",
    },
    {
      label: "all-time P&L",
      value: fmtUsdSigned(totalPnl),
      detail: fmtPctSigned(totalPct),
      emphasis: totalPnl > 0 ? "pos" : totalPnl < 0 ? "neg" : "neutral",
    },
    {
      label: "drawdown",
      value: `${(dd * 100).toFixed(2)}%`,
      detail:
        dd >= 0.1 ? "halt zone" : dd >= 0.05 ? "warn zone" : "ok",
      emphasis: dd >= 0.1 ? "neg" : dd >= 0.05 ? "neutral" : "pos",
    },
  ];

  return (
    <section className="relative overflow-hidden rounded-xl border border-border bg-gradient-to-br from-surface to-bg px-4 py-3 shadow-card-soft">
      {usingDemo && (
        <span className="absolute right-3 top-2 rounded-md border border-warn/30 bg-warn/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-warn">
          demo
        </span>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {stats.map((s, i) => {
          const emph =
            s.emphasis === "pos"
              ? "text-success"
              : s.emphasis === "neg"
              ? "text-danger"
              : "text-text";
          const glow =
            s.emphasis === "pos" ? pnlGlowClass(1) : s.emphasis === "neg" ? pnlGlowClass(-1) : "";
          return (
            <div
              key={s.label}
              className={`flex flex-col gap-0.5 ${i > 0 ? "md:border-l md:border-border md:pl-4" : ""}`}
            >
              <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-text-dim">
                {s.label}
              </span>
              <span className={`font-mono text-2xl font-semibold leading-none ${emph} ${glow}`}>
                {s.value}
              </span>
              {s.detail && (
                <span className="font-mono text-[10px] text-text-dim">{s.detail}</span>
              )}
            </div>
          );
        })}
      </div>
      {sparkPath && (
        <svg
          className="mt-2 h-6 w-full text-secondary/60"
          viewBox="0 0 96 28"
          preserveAspectRatio="none"
          aria-hidden
        >
          <polyline
            fill="none"
            stroke="currentColor"
            strokeWidth="0.6"
            strokeLinejoin="round"
            strokeLinecap="round"
            points={sparkPath}
          />
        </svg>
      )}
    </section>
  );
}
