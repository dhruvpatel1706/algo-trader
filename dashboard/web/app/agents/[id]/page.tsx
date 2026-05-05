"use client";
import { CoherenceGauge } from "@/components/CoherenceGauge";
import { TopBar } from "@/components/TopBar";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";

export default function AgentDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const decodedId = decodeURIComponent(id);
  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: () => api.agents() });
  const coherenceQ = useQuery({
    queryKey: ["coherence", decodedId],
    queryFn: () => api.coherence(decodedId),
  });

  // Backend identifies agents by `name`. The dynamic route is parameterized as
  // `id` purely for URL hygiene; we resolve it against `name`.
  const agent = (agentsQ.data ?? []).find((a) => a.name === decodedId);
  const coh = (coherenceQ.data ?? []).find((c) => c.agent === decodedId);

  return (
    <main className="min-h-screen bg-bg">
      <TopBar />
      <div className="mx-auto max-w-[1200px] space-y-4 p-6">
        <Link href="/agents" className="text-xs text-muted hover:text-zinc-200">
          ← all agents
        </Link>
        {!agent ? (
          <div className="rounded-lg border border-border bg-surface p-12 text-center text-muted">
            agent <span className="font-mono">{decodedId}</span> not found
          </div>
        ) : (
          <>
            <header className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h1 className="text-lg font-bold text-zinc-100">{agent.name}</h1>
                <p className="text-xs text-muted">
                  {agent.asset_class} · state {agent.state}
                </p>
              </div>
              <div className="flex gap-2 text-xs">
                <Stat
                  label="heat alloc"
                  value={`${(agent.heat_allocation * 100).toFixed(0)}%`}
                />
                <Stat
                  label="open positions"
                  value={agent.n_open_positions.toString()}
                />
              </div>
            </header>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <div className="rounded-lg border border-border bg-surface p-4 lg:col-span-2">
                <h2 className="mb-3 text-sm font-semibold text-zinc-200">activity</h2>
                <ul className="divide-y divide-border text-sm">
                  <li className="flex items-center justify-between px-2 py-3">
                    <span className="text-muted">last eval</span>
                    <span className="font-mono">{agent.last_eval_ts ?? "never"}</span>
                  </li>
                  <li className="flex items-center justify-between px-2 py-3">
                    <span className="text-muted">coherence</span>
                    <span className="font-mono">
                      {agent.coherence == null ? "—" : agent.coherence.toFixed(2)}
                    </span>
                  </li>
                </ul>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4">
                <h2 className="mb-3 text-sm font-semibold text-zinc-200">coherence</h2>
                {coh ? (
                  <CoherenceGauge
                    ratio={coh.ratio}
                    thresholdWarn={coh.threshold_warn}
                    thresholdHalt={coh.threshold_halt}
                    label={`live ${(coh.live_win_rate * 100).toFixed(1)}% / bt ${(coh.backtest_win_rate * 100).toFixed(1)}%`}
                  />
                ) : (
                  <CoherenceGauge
                    ratio={agent.coherence ?? 0}
                    label="snapshot"
                  />
                )}
              </div>
            </div>
          </>
        )}
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
    <div className="rounded-md bg-surface px-3 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`font-mono text-sm text-zinc-100 ${className}`}>{value}</div>
    </div>
  );
}
