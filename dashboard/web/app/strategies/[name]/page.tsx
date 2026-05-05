"use client";
import { HaltToggle } from "@/components/HaltToggle";
import { TopBar } from "@/components/TopBar";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { AreaSeries, createChart, IChartApi } from "lightweight-charts";
import Link from "next/link";
import { use, useEffect, useMemo, useRef } from "react";

export default function StrategyDetail({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);
  const decoded = decodeURIComponent(name);

  const strategies = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: api.metrics });
  const trades = useQuery({ queryKey: ["trades"], queryFn: () => api.trades() });
  const history = useQuery({
    queryKey: ["backtest-history", decoded],
    queryFn: () => api.backtestHistory(decoded),
  });

  const strategy = (strategies.data ?? []).find((s) => s.name === decoded);
  const m = metrics.data?.[decoded];
  const recent = useMemo(
    () =>
      (trades.data ?? [])
        .filter((t) => t.subject === decoded || t.symbol === decoded)
        .slice(-25)
        .reverse(),
    [trades.data, decoded],
  );
  // Backend `/api/backtest/history?strategy=<name>` already pre-filters to the
  // requested strategy, so we don't filter again — but we sort by run timestamp
  // so the chart renders oldest→newest.
  const sharpeRuns = useMemo(
    () =>
      (history.data ?? [])
        .slice()
        .sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())
        .filter((b): b is typeof b & { sharpe: number } => b.sharpe != null),
    [history.data],
  );

  const chartHost = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  useEffect(() => {
    if (!chartHost.current || chartRef.current) return;
    const chart = createChart(chartHost.current, {
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
    if (!chart || sharpeRuns.length === 0) return;
    const series = chart.addSeries(AreaSeries, {
      lineColor: "#3b82f6",
      topColor: "rgba(59, 130, 246, 0.3)",
      bottomColor: "rgba(59, 130, 246, 0.05)",
    });
    series.setData(
      sharpeRuns.map((r) => ({
        time: Math.floor(new Date(r.ts).getTime() / 1000) as never,
        value: r.sharpe,
      })),
    );
    return () => {
      chart.removeSeries(series);
    };
  }, [sharpeRuns]);

  return (
    <main className="min-h-screen bg-bg">
      <TopBar />
      <div className="mx-auto max-w-[1400px] space-y-4 p-6">
        <Link href="/strategies" className="text-xs text-muted hover:text-zinc-200">
          ← all strategies
        </Link>
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-bold text-zinc-100">{decoded}</h1>
            <p className="text-xs text-muted">
              {strategy ? (strategy.enabled ? "running" : "paused") : "not registered"}
            </p>
          </div>
          {strategy && <HaltToggle strategy={decoded} />}
        </header>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="trades" value={m ? String(m.n_trades) : "—"} />
          <Stat
            label="win rate"
            value={m ? `${(m.win_rate * 100).toFixed(1)}%` : "—"}
          />
          <Stat
            label="profit factor"
            value={m ? m.profit_factor.toFixed(2) : "—"}
          />
          <Stat label="expectancy" value={m ? m.expectancy.toFixed(4) : "—"} />
          <Stat
            label="total pnl"
            value={
              m
                ? `${m.total_pnl >= 0 ? "+" : ""}$${m.total_pnl.toFixed(2)}`
                : "—"
            }
            className={
              m ? (m.total_pnl >= 0 ? "text-accent" : "text-danger") : ""
            }
          />
        </div>

        <div className="rounded-lg border border-border bg-surface p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-200">sharpe trend</h2>
            <span className="text-xs text-muted">
              {sharpeRuns.length} backtest run(s)
            </span>
          </div>
          {sharpeRuns.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted">
              no backtest runs for this strategy yet
            </p>
          ) : (
            <div ref={chartHost} className="h-[260px] w-full" />
          )}
        </div>

        <div className="rounded-lg border border-border bg-surface">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold text-zinc-200">recent trades</h2>
          </div>
          <ul className="max-h-[360px] divide-y divide-border overflow-y-auto text-sm">
            {recent.length === 0 && (
              <li className="px-4 py-6 text-center text-muted">
                no trade events for this strategy
              </li>
            )}
            {recent.map((t, i) => (
              <li key={i} className="flex items-center gap-4 px-4 py-2 font-mono text-xs">
                <span className="w-44 truncate text-muted">{t.ts ?? "—"}</span>
                <span className="w-24 rounded bg-bg px-2 py-0.5 text-[10px] uppercase text-muted">
                  {t.event}
                </span>
                <span className="w-16 font-semibold">{t.symbol ?? "—"}</span>
                <span className="w-16 text-right">{t.qty ?? "—"}</span>
                <span className="w-12 uppercase text-muted">{t.side ?? "—"}</span>
                <span className="ml-auto text-xs text-muted">{t.status ?? ""}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </main>
  );
}

function Stat({
  label,
  value,
  className = "",
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className="rounded-md border border-border bg-surface p-3">
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`font-mono text-lg text-zinc-100 ${className}`}>{value}</div>
    </div>
  );
}
