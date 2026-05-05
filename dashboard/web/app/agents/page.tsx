"use client";
import { AgentCard } from "@/components/AgentCard";
import { CoherenceGauge } from "@/components/CoherenceGauge";
import { TopBar } from "@/components/TopBar";
import { api } from "@/lib/api";
import { useQueries } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";

// One coherence record per strategy. Until the runner publishes a strategies
// list, hardcoded against the wired set so the page renders something useful.
const KNOWN_STRATEGIES = [
  "mr_etf",
  "ma_pullback_trend",
  "failed_breakout",
  "range_shift_pullback",
  "momentum_xs",
];

export default function AgentsPage() {
  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: () => api.agents() });
  // Fan out coherence requests across the known strategy set so the gauge grid
  // shows one tile per strategy (not per agent — coherence is strategy-scoped).
  const coherenceQs = useQueries({
    queries: KNOWN_STRATEGIES.map((s) => ({
      queryKey: ["coherence", s],
      queryFn: () => api.coherence(s),
    })),
  });
  const list = agentsQ.data ?? [];
  // Flatten + filter to the coherence rows that actually came back.
  const coherenceRows = coherenceQs
    .flatMap((q) => q.data ?? [])
    .filter((c) => c.strategy);

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
        {coherenceRows.length > 0 && (
          <div className="rounded-lg border border-border bg-surface p-4">
            <h2 className="mb-1 text-sm font-semibold text-zinc-200">
              coherence — live vs backtest win rate
            </h2>
            <p className="mb-4 text-xs text-muted">
              one tile per strategy. ratio = live_win_rate / backtest_win_rate.
              null until live trading produces a sample.
            </p>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
              {coherenceRows.map((c) => (
                <CoherenceGauge
                  key={c.strategy}
                  ratio={c.coherence ?? 0}
                  label={`${c.strategy}${
                    c.coherence == null ? " (no live data)" : ""
                  }`}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
