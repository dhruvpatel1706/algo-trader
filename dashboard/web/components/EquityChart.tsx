"use client";
import { api } from "@/lib/api";
import type { EquityPoint } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { AreaSeries, createChart, IChartApi, LineSeries } from "lightweight-charts";
import { useEffect, useMemo, useRef } from "react";

type EquityChartProps = {
  /** When provided, overrides the default fetch. Sorted ascending by ts. */
  series?: EquityPoint[];
  /** When true, shade per-agent breakdown stacked below the total line. */
  stacked?: boolean;
  /** Title shown in the card header. */
  title?: string;
  /** Show drawdown overlay (red). */
  drawdown?: boolean;
  height?: number;
};

const AGENT_COLORS = ["#22c55e", "#3b82f6", "#a855f7", "#f59e0b", "#ec4899", "#06b6d4"];

/**
 * Equity curve. Supports three modes:
 *  1. `series` prop -> render exactly that series (joined equity, per-agent stacks)
 *  2. fall back to /api/portfolio/equity history (graceful empty state)
 *  3. fall back to broker {last_equity, equity} 2-point sketch (v1 behavior)
 */
export function EquityChart({
  series,
  stacked = false,
  title = "equity curve",
  drawdown = false,
  height = 280,
}: EquityChartProps = {}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const equity = useQuery({
    queryKey: ["equity"],
    queryFn: api.equity,
    enabled: !series,
  });
  const portfolio = useQuery({
    queryKey: ["portfolio"],
    queryFn: api.portfolio,
    enabled: !series,
  });

  const points: EquityPoint[] = useMemo(() => {
    if (series && series.length > 0) return series;
    if (equity.data && equity.data.length > 0) return equity.data;
    const p = portfolio.data;
    if (p?.equity) {
      const now = Math.floor(Date.now() / 1000);
      return [
        {
          ts: new Date((now - 86400) * 1000).toISOString(),
          total: p.last_equity ?? p.equity,
        },
        { ts: new Date(now * 1000).toISOString(), total: p.equity },
      ];
    }
    return [];
  }, [series, equity.data, portfolio.data]);
  const isEmpty = points.length === 0;

  useEffect(() => {
    if (!ref.current || chartRef.current) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: "#13161b" }, textColor: "#a1a1aa" },
      grid: {
        vertLines: { color: "#1f242c" },
        horzLines: { color: "#1f242c" },
      },
      timeScale: { borderColor: "#1f242c", timeVisible: true },
      rightPriceScale: { borderColor: "#1f242c" },
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || points.length === 0) return;

    const total = chart.addSeries(AreaSeries, {
      lineColor: "#22c55e",
      topColor: "rgba(34, 197, 94, 0.3)",
      bottomColor: "rgba(34, 197, 94, 0.05)",
    });
    total.setData(
      points.map((p) => ({
        time: tsToUnix(p.ts) as never,
        value: p.total,
      })),
    );

    const extras: { remove: () => void }[] = [];

    if (stacked) {
      const agents = new Set<string>();
      for (const p of points) {
        if (p.by_agent) Object.keys(p.by_agent).forEach((a) => agents.add(a));
      }
      let i = 0;
      for (const agent of agents) {
        const s = chart.addSeries(LineSeries, {
          color: AGENT_COLORS[i % AGENT_COLORS.length],
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        i += 1;
        s.setData(
          points
            .filter((p) => p.by_agent && agent in p.by_agent)
            .map((p) => ({
              time: tsToUnix(p.ts) as never,
              value: (p.by_agent ?? {})[agent] ?? 0,
            })),
        );
        extras.push({ remove: () => chart.removeSeries(s) });
      }
    }

    if (drawdown) {
      const dd = chart.addSeries(AreaSeries, {
        lineColor: "#ef4444",
        topColor: "rgba(239, 68, 68, 0.25)",
        bottomColor: "rgba(239, 68, 68, 0.0)",
        priceScaleId: "left",
      });
      chart.priceScale("left").applyOptions({ visible: false });
      dd.setData(
        points
          .filter((p) => typeof p.drawdown === "number")
          .map((p) => ({
            time: tsToUnix(p.ts) as never,
            value: p.drawdown ?? 0,
          })),
      );
      extras.push({ remove: () => chart.removeSeries(dd) });
    }

    return () => {
      // Lightweight-charts can raise if the chart was already disposed by the
      // outer effect (React StrictMode double-mounts). Swallow — these are
      // cleanup ops, not user-visible.
      try {
        extras.forEach((e) => {
          try {
            e.remove();
          } catch {}
        });
        chart.removeSeries(total);
      } catch {}
    };
  }, [points, stacked, drawdown]);

  return (
    <div className="rounded-2xl border border-border bg-surface p-5 shadow-card-soft">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold tracking-wide text-text">{title}</h2>
      </div>
      {isEmpty ? (
        <div
          style={{ height }}
          className="flex flex-col items-center justify-center text-center"
        >
          <p className="font-mono text-[12px] text-text-dim">
            no equity history yet
          </p>
          <p className="mt-1 font-mono text-[10px] text-muted">
            connect Alpaca (set ALPACA_API_KEY) and start the bot to populate this curve
          </p>
        </div>
      ) : (
        <div ref={ref} style={{ height }} className="w-full" />
      )}
    </div>
  );
}

function tsToUnix(ts: string): number {
  const n = Number(ts);
  if (!Number.isNaN(n) && n > 0) return n > 1e12 ? Math.floor(n / 1000) : n;
  return Math.floor(new Date(ts).getTime() / 1000);
}
