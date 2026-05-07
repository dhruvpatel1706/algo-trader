"use client";
import { api } from "@/lib/api";
import type { EquityPoint } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

/** Defensive money formatter — never crashes on null/undefined/NaN. */
function fmtMoney(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return n.toLocaleString();
}

/**
 * Computes current drawdown from /api/portfolio/equity series and shows a
 * horizontal bar gauge. Renders an empty state when the equity history is
 * empty rather than fabricating a demo curve.
 */
export function DrawdownGauge({
  warnAt = 0.05,
  haltAt = 0.1,
}: { warnAt?: number; haltAt?: number } = {}) {
  const equity = useQuery({ queryKey: ["equity"], queryFn: api.equity });
  const series: EquityPoint[] = equity.data ?? [];
  const isEmpty = series.length === 0;

  const { dd, peak, last } = useMemo(() => {
    if (series.length === 0) return { dd: 0, peak: 0, last: 0 };
    let peakVal = -Infinity;
    let lastVal = 0;
    for (const p of series) {
      // Defend against EquityPoint rows with missing/null `total` — the API
      // can produce these for stub days before the bot has populated equity
      // history. Without this guard ``last`` ends up undefined and the
      // ``last.toLocaleString()`` call below blows up.
      if (typeof p.total !== "number" || !Number.isFinite(p.total)) continue;
      if (p.total > peakVal) peakVal = p.total;
      lastVal = p.total;
    }
    // If no usable data was found, fall back to zeros (matches empty branch).
    if (!Number.isFinite(peakVal)) return { dd: 0, peak: 0, last: 0 };
    const ddVal = peakVal > 0 ? (peakVal - lastVal) / peakVal : 0;
    return { dd: ddVal, peak: peakVal, last: lastVal };
  }, [series]);

  const ratio = Math.min(1, dd / Math.max(haltAt, 0.0001));
  const color = dd >= haltAt ? "#ef4444" : dd >= warnAt ? "#f59e0b" : "#22c55e";

  return (
    <div className="rounded-2xl border border-border bg-surface p-5 shadow-card-soft">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold tracking-wide text-text">drawdown</h2>
      </div>
      {isEmpty ? (
        <p className="py-6 text-center font-mono text-[11px] text-muted">
          no equity history — start the bot to populate
        </p>
      ) : (
        <>
          <div className="mb-2 flex items-baseline justify-between">
            <span className="font-mono text-2xl text-zinc-100">
              {(dd * 100).toFixed(2)}%
            </span>
            <span className="text-xs text-muted">
              peak ${fmtMoney(peak)} · last ${fmtMoney(last)}
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
