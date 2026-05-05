"use client";

import { demoTrades, type DemoTrade } from "@/lib/demo";
import {
  fmtRelative,
  fmtTime,
  fmtUsd,
  fmtUsdSigned,
  fmtPctSigned,
  pnlColorClass,
} from "@/lib/format";
import { api } from "@/lib/api";
import type { Trade } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

type Row = DemoTrade;

function StateBadge({ state }: { state: DemoTrade["state"] }) {
  const map: Record<DemoTrade["state"], { label: string; cls: string }> = {
    open: { label: "open", cls: "border-info/40 bg-info/10 text-info" },
    closed_win: { label: "win", cls: "border-success/40 bg-success/10 text-success" },
    closed_loss: { label: "loss", cls: "border-danger/40 bg-danger/10 text-danger" },
    stopped: { label: "stop", cls: "border-warn/40 bg-warn/10 text-warn" },
  };
  const { label, cls } = map[state];
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${cls}`}
    >
      {label}
    </span>
  );
}

function SideBadge({ side }: { side: DemoTrade["side"] }) {
  return (
    <span
      className={`font-mono text-[10px] font-semibold uppercase tracking-wider ${
        side === "buy" ? "text-success" : "text-danger"
      }`}
    >
      {side === "buy" ? "▲ long" : "▼ short"}
    </span>
  );
}

function FilterPill({ label }: { label: string }) {
  // simple visual differentiation by namespace prefix
  const [ns] = label.split(":");
  const cls =
    ns === "regime"
      ? "border-secondary/30 bg-secondary/5 text-secondary"
      : ns === "insider"
      ? "border-accent/30 bg-accent/5 text-accent"
      : ns === "news"
      ? "border-info/30 bg-info/5 text-info"
      : ns === "ml"
      ? "border-warn/30 bg-warn/5 text-warn"
      : "border-border bg-surface text-text-dim";
  return (
    <span
      className={`inline-flex h-5 items-center rounded border px-1.5 font-mono text-[10px] ${cls}`}
    >
      {label}
    </span>
  );
}

export function LiveTradesFeed() {
  // Try journal-derived trades first, fall back to demo when empty.
  const tradesQ = useQuery({
    queryKey: ["trades-feed"],
    queryFn: () => api.trades(),
    refetchInterval: 5_000,
  });
  const realTrades: Trade[] = tradesQ.data ?? [];
  // Journal records from a bare-bones smoke test ("submit_dry_run" with no prices)
  // are not useful here — fall back to demo so the operator can see what a populated
  // feed looks like. Only show real trades when at least one has entry-price-like data.
  const realRich = realTrades.filter(
    (t) =>
      t.symbol &&
      t.symbol !== "—" &&
      (t.pnl != null || (t.qty ?? 0) > 0) &&
      t.event !== "submit_dry_run",
  );
  const usingDemo = realRich.length < 3;
  const rows: Row[] = useMemo(() => (usingDemo ? demoTrades() : adaptTrades(realRich)), [
    usingDemo,
    realRich,
  ]);

  const [filter, setFilter] = useState<"all" | "open" | "closed">("all");
  const visible = rows.filter((r) =>
    filter === "all" ? true : filter === "open" ? r.state === "open" : r.state !== "open",
  );

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold tracking-wide text-text">live trades</h2>
          {usingDemo && (
            <span className="rounded border border-warn/30 bg-warn/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-warn">
              demo
            </span>
          )}
          <span className="font-mono text-[10px] text-text-dim">
            {visible.length}/{rows.length}
          </span>
        </div>
        <div className="flex gap-1 rounded-md border border-border bg-bg p-0.5">
          {(["all", "open", "closed"] as const).map((k) => (
            <button
              type="button"
              key={k}
              onClick={() => setFilter(k)}
              className={`rounded px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider transition ${
                filter === k
                  ? "bg-surface-2 text-text shadow-card-soft"
                  : "text-text-dim hover:text-text"
              }`}
            >
              {k}
            </button>
          ))}
        </div>
      </header>
      <div className="max-h-[480px] overflow-x-auto overflow-y-auto">
        <table className="min-w-full font-mono text-[11px]">
          <thead className="sticky top-0 bg-surface text-text-dim">
            <tr className="font-mono text-[10px] uppercase tracking-wider">
              <th className="px-3 py-1.5 text-left">time</th>
              <th className="px-3 py-1.5 text-left">symbol</th>
              <th className="px-3 py-1.5 text-left">side</th>
              <th className="px-3 py-1.5 text-left">agent · strategy</th>
              <th className="px-3 py-1.5 text-right">qty</th>
              <th className="px-3 py-1.5 text-right">entry</th>
              <th className="px-3 py-1.5 text-right">stop</th>
              <th className="px-3 py-1.5 text-right">target</th>
              <th className="px-3 py-1.5 text-right">P&L $</th>
              <th className="px-3 py-1.5 text-right">P&L %</th>
              <th className="px-3 py-1.5 text-left">filters</th>
              <th className="px-3 py-1.5 text-right">state</th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={12} className="py-12 text-center text-sm text-text-dim">
                  no trades match
                </td>
              </tr>
            ) : (
              visible.map((r) => (
                <tr
                  key={r.id}
                  className="border-t border-border/60 transition hover:bg-surface-2"
                >
                  <td className="whitespace-nowrap px-3 py-1 font-mono text-text-dim" title={r.ts}>
                    <span className="text-text">{fmtTime(r.ts)}</span>
                    <span className="ml-1.5 text-[10px] text-text-dim/70">{fmtRelative(r.ts)}</span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-1 font-mono font-semibold text-text">
                    {r.symbol}
                  </td>
                  <td className="px-3 py-1">
                    <SideBadge side={r.side} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-1 text-text-dim">
                    <span className="text-text">{r.agent}</span>
                    <span className="ml-1.5 text-[10px] text-text-dim">{r.strategy}</span>
                  </td>
                  <td className="px-3 py-1 text-right text-text">{r.qty}</td>
                  <td className="px-3 py-1 text-right text-text">
                    {fmtUsd(r.entry)}
                  </td>
                  <td className="px-3 py-1 text-right text-text-dim">
                    {fmtUsd(r.stop)}
                  </td>
                  <td className="px-3 py-1 text-right text-text-dim">
                    {fmtUsd(r.target)}
                  </td>
                  <td className={`px-3 py-1 text-right ${pnlColorClass(r.pnl)}`}>
                    {r.pnl == null ? "—" : fmtUsdSigned(r.pnl)}
                  </td>
                  <td className={`px-3 py-1 text-right ${pnlColorClass(r.pnl_pct)}`}>
                    {r.pnl_pct == null ? "—" : fmtPctSigned(r.pnl_pct)}
                  </td>
                  <td className="px-3 py-1">
                    <div className="flex flex-wrap gap-0.5">
                      {r.filter_status.length === 0 ? (
                        <span className="text-text-dim/60">—</span>
                      ) : (
                        r.filter_status.map((f) => <FilterPill key={f} label={f} />)
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-1 text-right">
                    <StateBadge state={r.state} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function adaptTrades(trades: Trade[]): Row[] {
  // Best-effort adapter from journal-style records to feed rows.
  return trades
    .filter((t) => t.symbol)
    .slice(0, 25)
    .map((t, i) => ({
      id: t.cycle_id ?? `t-${i}`,
      ts: t.ts ?? new Date().toISOString(),
      agent: "—",
      strategy: t.event ?? "—",
      symbol: t.symbol ?? "—",
      side: (t.side === "sell" ? "sell" : "buy") as "buy" | "sell",
      qty: Math.abs(t.qty ?? 0),
      entry: 0,
      stop: 0,
      target: 0,
      exit: null,
      pnl: t.pnl ?? null,
      pnl_pct: null,
      state:
        t.status === "filled"
          ? (t.pnl ?? 0) > 0
            ? "closed_win"
            : (t.pnl ?? 0) < 0
            ? "closed_loss"
            : "open"
          : "open",
      confidence: 0,
      filter_status: [],
    }));
}
