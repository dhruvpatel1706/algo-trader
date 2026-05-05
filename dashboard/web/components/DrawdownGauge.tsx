"use client";
import { api } from "@/lib/api";
import { demoEquity } from "@/lib/demo";
import type { EquityPoint } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

/**
 * Computes current drawdown from /api/portfolio/equity series and shows a
 * horizontal bar gauge. Falls back to "no data yet" when series is empty.
 */
export function DrawdownGauge({
  warnAt = 0.05,
  haltAt = 0.1,
}: { warnAt?: number; haltAt?: number } = {}) {
  const equity = useQuery({ queryKey: ["equity"], queryFn: api.equity });
  const real: EquityPoint[] = equity.data ?? [];
  const usingDemo = real.length === 0;
  const series: EquityPoint[] = usingDemo
    ? demoEquity().map((d) => ({ ts: d.ts, total: d.total, drawdown: d.drawdown }))
    : real;

  const { dd, peak, last } = useMemo(() => {
    if (series.length === 0) return { dd: 0, peak: 0, last: 0 };
    let peakVal = -Infinity;
    let lastVal = 0;
    for (const p of series) {
      if (p.total > peakVal) peakVal = p.total;
      lastVal = p.total;
    }
    const ddVal = peakVal > 0 ? (peakVal - lastVal) / peakVal : 0;
    return { dd: ddVal, peak: peakVal, last: lastVal };
  }, [series]);

  const ratio = Math.min(1, dd / Math.max(haltAt, 0.0001));
  const color = dd >= haltAt ? "#ef4444" : dd >= warnAt ? "#f59e0b" : "#22c55e";

  return (
    <div className="rounded-2xl border border-border bg-surface p-5 shadow-card-soft">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold tracking-wide text-text">drawdown</h2>
        {usingDemo && (
          <span className="rounded border border-warn/30 bg-warn/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-warn">
            demo
          </span>
        )}
      </div>
      {series.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">no data yet</p>
      ) : (
        <>
          <div className="mb-2 flex items-baseline justify-between">
            <span className="font-mono text-2xl text-zinc-100">
              {(dd * 100).toFixed(2)}%
            </span>
            <span className="text-xs text-muted">
              peak ${peak.toLocaleString()} · last ${last.toLocaleString()}
            </span>
          </div>
          <div className="relative h-3 w-full overflow-hidden rounded-full bg-bg">
            <div
              className="h-full transition-all"
              style={{ width: `${ratio * 100}%`, background: color }}
            />
            <div
              className="absolute top-0 h-full w-px bg-warn"
              style={{ left: `${(warnAt / haltAt) * 100}%` }}
            />
            <div className="absolute right-0 top-0 h-full w-px bg-danger" />
          </div>
          <div className="mt-2 flex justify-between text-[10px] text-muted">
            <span>0%</span>
            <span>warn {Math.round(warnAt * 100)}%</span>
            <span>halt {Math.round(haltAt * 100)}%</span>
          </div>
        </>
      )}
    </div>
  );
}
