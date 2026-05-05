"use client";
import { api } from "@/lib/api";
import type { Signal } from "@/lib/types";
import { useSignalStream } from "@/lib/ws";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

export function SignalStream() {
  const seed = useQuery({
    queryKey: ["signals-recent"],
    queryFn: () => api.recentSignals(),
  });
  const { signals, wsState } = useSignalStream(seed.data ?? [], 200);

  const [agentFilter, setAgentFilter] = useState<string>("all");
  const [sideFilter, setSideFilter] = useState<"all" | "buy" | "sell" | "flat">("all");
  const [symbolFilter, setSymbolFilter] = useState<string>("");

  const agents = useMemo(() => {
    const set = new Set<string>();
    signals.forEach((s) => set.add(s.agent));
    return ["all", ...Array.from(set).sort()];
  }, [signals]);

  const filtered = useMemo(() => {
    return signals.filter((s) => {
      if (agentFilter !== "all" && s.agent !== agentFilter) return false;
      if (sideFilter !== "all" && s.side !== sideFilter) return false;
      if (symbolFilter && !s.symbol.toLowerCase().includes(symbolFilter.toLowerCase()))
        return false;
      return true;
    });
  }, [signals, agentFilter, sideFilter, symbolFilter]);

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">signals stream</h2>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
            wsState === "open"
              ? "bg-accent/20 text-accent"
              : wsState === "connecting"
                ? "bg-warn/20 text-warn"
                : "bg-zinc-700/50 text-muted"
          }`}
        >
          ws {wsState}
        </span>
      </div>
      <div className="flex flex-wrap gap-3 border-b border-border px-4 py-2">
        <Select label="agent" value={agentFilter} onChange={setAgentFilter} options={agents} />
        <Select
          label="side"
          value={sideFilter}
          onChange={(v) => setSideFilter(v as typeof sideFilter)}
          options={["all", "buy", "sell", "flat"]}
        />
        <label className="flex items-center gap-2 text-xs text-muted">
          symbol
          <input
            value={symbolFilter}
            onChange={(e) => setSymbolFilter(e.target.value)}
            placeholder="filter"
            className="w-32 rounded-md border border-border bg-bg px-2 py-1 font-mono text-zinc-100 focus:border-zinc-500 focus:outline-none"
          />
        </label>
      </div>
      <ul className="max-h-[480px] divide-y divide-border overflow-y-auto text-sm">
        {filtered.length === 0 && (
          <li className="px-4 py-6 text-center text-muted">no signals yet</li>
        )}
        {filtered.map((s, i) => (
          <SignalRow key={`${s.id}-${i}`} signal={s} />
        ))}
      </ul>
    </div>
  );
}

function SignalRow({ signal }: { signal: Signal }) {
  const sideClass =
    signal.side === "buy"
      ? "bg-accent/20 text-accent"
      : signal.side === "sell"
        ? "bg-danger/20 text-danger"
        : "bg-zinc-700/50 text-muted";
  const conf = (signal.confidence ?? 0) * 100;
  return (
    <li className="flex items-center gap-4 px-4 py-2 font-mono text-xs">
      <span className="w-44 truncate text-muted">{signal.ts}</span>
      <span className="w-28 truncate text-zinc-200">{signal.agent}</span>
      <span className="w-32 truncate text-muted">{signal.strategy}</span>
      <span className="w-16 font-semibold text-zinc-100">{signal.symbol}</span>
      <span
        className={`w-12 rounded px-1 py-0.5 text-center text-[10px] uppercase ${sideClass}`}
      >
        {signal.side}
      </span>
      <span className="w-16 text-right text-muted">{conf.toFixed(0)}%</span>
      <span className="flex-1 truncate text-muted">{signal.reason ?? ""}</span>
    </li>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-muted">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-border bg-bg px-2 py-1 font-mono text-zinc-100 focus:border-zinc-500 focus:outline-none"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}
