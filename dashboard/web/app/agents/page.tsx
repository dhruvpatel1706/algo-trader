"use client";
import { AgentCard } from "@/components/AgentCard";
import { CoherenceGauge } from "@/components/CoherenceGauge";
import { TopBar } from "@/components/TopBar";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

export default function AgentsPage() {
  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: () => api.agents() });
  // /api/coherence requires a strategy query param; fan out across the visible agents.
  const coherenceQ = useQuery({
    queryKey: ["coherence", "mr_etf"],
    queryFn: () => api.coherence("mr_etf"),
  });
  const list = agentsQ.data ?? [];
  const coherenceList = coherenceQ.data ?? [];

  return (
    <main className="min-h-screen bg-bg">
      <TopBar />
      <div className="mx-auto max-w-[1600px] space-y-4 p-6">
        <div>
          <h1 className="text-lg font-bold text-zinc-100">agents</h1>
          <p className="text-sm text-muted">
            click an agent to see its strategies and coherence detail
          </p>
        </div>
        {list.length === 0 ? (
          <div className="rounded-lg border border-border bg-surface p-12 text-center text-muted">
            no agents registered yet
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {list.map((a) => (
              <AgentCard key={a.name} agent={a} />
            ))}
          </div>
        )}
        {coherenceList.length > 0 && (
          <div className="rounded-lg border border-border bg-surface p-4">
            <h2 className="mb-4 text-sm font-semibold text-zinc-200">
              coherence — live vs backtest win rate
            </h2>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
              {coherenceList.map((c) => (
                <CoherenceGauge
                  key={c.agent}
                  ratio={c.ratio}
                  label={c.agent}
                  thresholdWarn={c.threshold_warn}
                  thresholdHalt={c.threshold_halt}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
