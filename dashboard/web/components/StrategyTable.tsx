"use client";
import { api } from "@/lib/api";
import type { StrategyStatus, TrailingMetrics } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";

type SortKey = "name" | "trades" | "win_rate" | "expectancy" | "pnl";

function fmt(n: number | undefined, d = 2) {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

export function StrategyTable() {
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: api.metrics });
  const [sort, setSort] = useState<SortKey>("name");
  const [dir, setDir] = useState<"asc" | "desc">("asc");

  const rows = useMemo(() => {
    const list = strategies.data ?? [];
    const m: TrailingMetrics = metrics.data ?? {};
    const merged = list.map((s: StrategyStatus) => ({
      ...s,
      ...(m[s.name] ?? { n_trades: 0, win_rate: 0, profit_factor: 0, expectancy: 0, total_pnl: 0 }),
    }));
    merged.sort((a, b) => {
      let cmp = 0;
      switch (sort) {
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "trades":
          cmp = (a.n_trades ?? 0) - (b.n_trades ?? 0);
          break;
        case "win_rate":
          cmp = (a.win_rate ?? 0) - (b.win_rate ?? 0);
          break;
        case "expectancy":
          cmp = (a.expectancy ?? 0) - (b.expectancy ?? 0);
          break;
        case "pnl":
          cmp = (a.total_pnl ?? 0) - (b.total_pnl ?? 0);
          break;
      }
      return dir === "asc" ? cmp : -cmp;
    });
    return merged;
  }, [strategies.data, metrics.data, sort, dir]);

  const head = (key: SortKey, label: string) => (
    <th
      className="cursor-pointer px-4 py-2 text-left hover:text-zinc-200"
      onClick={() => {
        if (sort === key) setDir(dir === "asc" ? "desc" : "asc");
        else {
          setSort(key);
          setDir("asc");
        }
      }}
    >
      {label} {sort === key && (dir === "asc" ? "↑" : "↓")}
    </th>
  );

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">strategies</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs uppercase tracking-wider text-muted">
            <tr>
              {head("name", "name")}
              <th className="px-4 py-2 text-left">status</th>
              {head("trades", "trades")}
              {head("win_rate", "win rate")}
              {head("expectancy", "expectancy")}
              {head("pnl", "pnl")}
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-muted">
                  no strategies registered yet
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.name} className="border-t border-border font-mono">
                <td className="px-4 py-2 font-semibold text-zinc-100">{r.name}</td>
                <td className="px-4 py-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs ${
                      r.enabled
                        ? "bg-accent/20 text-accent"
                        : "bg-zinc-700/50 text-muted"
                    }`}
                  >
                    {r.enabled ? "enabled" : "paused"}
                  </span>
                </td>
                <td className="px-4 py-2">{r.n_trades ?? 0}</td>
                <td className="px-4 py-2">
                  {r.win_rate !== undefined ? `${(r.win_rate * 100).toFixed(1)}%` : "—"}
                </td>
                <td className="px-4 py-2">{fmt(r.expectancy, 4)}</td>
                <td
                  className={`px-4 py-2 ${
                    (r.total_pnl ?? 0) >= 0 ? "text-accent" : "text-danger"
                  }`}
                >
                  {(r.total_pnl ?? 0) >= 0 ? "+" : ""}${fmt(r.total_pnl)}
                </td>
                <td className="px-4 py-2 text-right">
                  <Link
                    href={`/strategies/${encodeURIComponent(r.name)}`}
                    className="text-xs text-muted hover:text-zinc-200"
                  >
                    detail →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
