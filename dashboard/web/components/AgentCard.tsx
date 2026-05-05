"use client";
import type { Agent, AssetClass } from "@/lib/types";
import Link from "next/link";

const ASSET_ICON: Record<AssetClass, string> = {
  equity: "EQ",
  gold: "AU",
  silver: "AG",
  bonds: "BND",
  crypto: "BTC",
  governance: "GOV",
  fx: "FX",
  options: "OPT",
  futures: "FUT",
  other: "•",
};

function pctClass(n: number | null) {
  if (n == null) return "text-muted";
  if (n >= 0.95) return "text-success";
  if (n >= 0.7) return "text-warn";
  return "text-danger";
}

function fmtRelative(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86_400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86_400)}d ago`;
}

const STATE_BADGE: Record<string, string> = {
  paper: "bg-info/20 text-info",
  live: "bg-success/20 text-success",
  halted: "bg-danger/20 text-danger",
  warmup: "bg-warn/20 text-warn",
  active: "bg-success/20 text-success",
};

export function AgentCard({ agent }: { agent: Agent }) {
  const heatPct = (agent.heat_allocation * 100).toFixed(0);
  const stateClass = STATE_BADGE[agent.state] ?? "bg-zinc-700/50 text-muted";

  return (
    <Link
      href={`/agents/${encodeURIComponent(agent.name)}`}
      className="block rounded-lg border border-border bg-surface p-4 transition-colors hover:border-zinc-600"
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded-md bg-bg px-2 py-1 font-mono text-[10px] uppercase text-muted">
            {ASSET_ICON[agent.asset_class] ?? agent.asset_class}
          </span>
          <span className="font-semibold text-zinc-100">{agent.name}</span>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${stateClass}`}
        >
          {agent.state}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Stat label="heat alloc" value={`${heatPct}%`} />
        <Stat label="open pos" value={agent.n_open_positions.toString()} />
        <Stat
          label="coherence"
          value={agent.coherence == null ? "—" : agent.coherence.toFixed(2)}
          className={pctClass(agent.coherence)}
        />
        <Stat label="last eval" value={fmtRelative(agent.last_eval_ts)} />
      </div>
    </Link>
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
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-muted">{label}</span>
      <span className={`font-mono text-zinc-100 ${className}`}>{value}</span>
    </div>
  );
}
