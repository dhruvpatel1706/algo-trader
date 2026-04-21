"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { AreaSeries, createChart, IChartApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

/**
 * Equity curve. v1 reads `last_equity` and `equity` from the broker each tick to
 * sketch a 2-point line; replace with a real history feed once it's wired.
 */
export function EquityChart() {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const portfolio = useQuery({ queryKey: ["portfolio"], queryFn: api.portfolio });

  useEffect(() => {
    if (!ref.current || chartRef.current) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: "#13161b" }, textColor: "#a1a1aa" },
      grid: {
        vertLines: { color: "#1f242c" },
        horzLines: { color: "#1f242c" },
      },
      timeScale: { borderColor: "#1f242c" },
      rightPriceScale: { borderColor: "#1f242c" },
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current || !portfolio.data?.equity) return;
    const series = chartRef.current.addSeries(AreaSeries, {
      lineColor: "#22c55e",
      topColor: "rgba(34, 197, 94, 0.3)",
      bottomColor: "rgba(34, 197, 94, 0.05)",
    });
    const now = Math.floor(Date.now() / 1000);
    const yesterday = now - 86400;
    series.setData([
      { time: yesterday as never, value: portfolio.data.last_equity ?? portfolio.data.equity },
      { time: now as never, value: portfolio.data.equity },
    ]);
    return () => {
      chartRef.current?.removeSeries(series);
    };
  }, [portfolio.data?.equity, portfolio.data?.last_equity]);

  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">equity curve</h2>
        <span className="text-xs text-muted">
          (v1: 2-point sketch — full history lands with TimescaleDB)
        </span>
      </div>
      <div ref={ref} className="h-[280px] w-full" />
    </div>
  );
}
