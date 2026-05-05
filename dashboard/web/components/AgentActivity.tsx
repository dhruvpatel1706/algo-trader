"use client";

import { api } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";

type AgentRow = {
  name: string;
  asset_class: string;
  state: string;
  heat_allocation: number;
  coherence: number | null;
  n_open_positions: number;
  last_eval_ts: string | null;
};

const ASSET_ICON: Record<string, string> = {
  equity: "▤",
  gold: "◇",
  silver: "◈",
  bonds: "▭",
  crypto: "◊",
  governance: "⊙",
};

const ASSET_LABEL: Record<string, string> = {
  equity: "Equities",
  gold: "Gold",
  silver: "Silver",
  bonds: "Bonds",
  crypto: "Crypto",
  governance: "Governance",
};

const STATE_BADGE: Record<string, string> = {
  paper: "border-info/40 bg-info/10 text-info",
  live: "border-success/40 bg-success/10 text-success",
  halted: "border-danger/40 bg-danger/10 text-danger",
  warmup: "border-warn/40 bg-warn/10 text-warn",
  active: "border-success/40 bg-success/10 text-success",
};

// Fallback activity descriptions when no real data — lets operator see what each
// bot does even before any signals fire.
const ACTIVITY_HINT: Record<string, string> = {
  equity_agent: "scanning liquid_etfs_top20 + large_caps_50 for mr_etf, ma_pullback_trend, failed_breakout, momentum_xs",
  gold_agent: "watching GLD/IAU/GDX for failed-breakdown rejection + 20/200 SMA pullback",
  bonds_agent: "tracking TLT/IEF/AGG/BND with macro_regime_filter; risk-off bias scaling",
  crypto_agent: "polling BTCUSDT/ETHUSDT 1h+4h via SimulatedCryptoBroker (paper)",
  governance_agent: "hourly: scoring strategy coherence, kill/promote candidates, drift checks",
};

function useAgents() {
  return useQuery({
    queryKey: ["agents"],
    queryFn: () => api.agents(),
    refetchInterval: 10_000,
  });
}

export function AgentActivity() {
  const agentsQ = useAgents();
  // Backend returns AgentRow shape (name/asset_class/state/...). The Agent type
  // in lib/types is the v2 model (id/strategies/...) — we cast through unknown to
  // bridge until the two shapes converge.
  const agents: AgentRow[] = ((agentsQ.data ?? []) as unknown) as AgentRow[];

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex items-center justify-between border-b border-border px-5 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold tracking-wide text-text">agent activity</h2>
          <span className="font-mono text-[11px] text-text-dim">
            5 agents · refresh 10s
          </span>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
          observation only · no execution from this UI
        </span>
      </header>
      <div className="grid grid-cols-1 divide-y divide-border md:grid-cols-2 md:divide-x md:divide-y-0 lg:grid-cols-5">
        {agents.length === 0 ? (
          <div className="col-span-5 py-10 text-center text-sm text-text-dim">
            no agents reachable — start the runner via{" "}
            <code className="rounded bg-bg px-1.5 py-0.5 font-mono text-[11px]">scripts/run_bot.py</code>
          </div>
        ) : (
          agents.map((a) => {
            const stateClass = STATE_BADGE[a.state] ?? "border-border bg-bg text-text-dim";
            const heatPct = (a.heat_allocation * 100).toFixed(0);
            return (
              <div key={a.name} className="px-4 py-4">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-lg text-secondary">
                      {ASSET_ICON[a.asset_class] ?? "•"}
                    </span>
                    <div>
                      <div className="font-mono text-[11px] uppercase tracking-wider text-text-dim">
                        {ASSET_LABEL[a.asset_class] ?? a.asset_class}
                      </div>
                      <div className="font-mono text-sm font-semibold text-text">{a.name}</div>
                    </div>
                  </div>
                  <span
                    className={`rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${stateClass}`}
                  >
                    {a.state}
                  </span>
                </div>
                <div className="mt-3 space-y-1.5">
                  <Row label="heat" value={`${heatPct}%`} />
                  <Row
                    label="coherence"
                    value={a.coherence == null ? "—" : a.coherence.toFixed(2)}
                  />
                  <Row label="open" value={a.n_open_positions.toString()} />
                  <Row
                    label="last eval"
                    value={a.last_eval_ts ? fmtRelative(a.last_eval_ts) : "never"}
                  />
                </div>
                <p className="mt-3 font-mono text-[10px] leading-relaxed text-text-dim">
                  {ACTIVITY_HINT[a.name] ?? "no activity hint"}
                </p>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between font-mono text-[11px]">
      <span className="text-text-dim">{label}</span>
      <span className="text-text">{value}</span>
    </div>
  );
}

/**
 * Compact vertical sidebar variant — same data as AgentActivity but stacked
 * one-row-per-agent. Designed for the right column of the equity-chart row.
 *
 * Always renders the canonical 5-agent skeleton (eq/gold/bonds/crypto/gov);
 * if the API is empty or partial we still show every slot greyed out so the
 * operator sees the full topology at a glance.
 */
const AGENT_SLOTS: { name: string; asset_class: string }[] = [
  { name: "equity_agent",     asset_class: "equity" },
  { name: "gold_agent",       asset_class: "gold" },
  { name: "silver_agent",     asset_class: "silver" },
  { name: "bonds_agent",      asset_class: "bonds" },
  { name: "crypto_agent",     asset_class: "crypto" },
  { name: "governance_agent", asset_class: "governance" },
];

const SLOT_LABEL: Record<string, string> = {
  equity_agent: "EQ",
  gold_agent: "AU",
  silver_agent: "AG",
  bonds_agent: "BD",
  crypto_agent: "CR",
  governance_agent: "GV",
};

export function AgentSidebar() {
  const agentsQ = useAgents();
  const live = ((agentsQ.data ?? []) as unknown) as AgentRow[];
  const byName = new Map(live.map((a) => [a.name, a]));

  return (
    <section className="flex h-full flex-col rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex items-center justify-between border-b border-border px-3 py-2">
        <h2 className="font-mono text-[11px] font-semibold uppercase tracking-wider text-text">
          agents
        </h2>
        <span className="font-mono text-[9px] uppercase tracking-wider text-text-dim">
          5 · 10s
        </span>
      </header>
      <ul className="flex flex-1 flex-col divide-y divide-border">
        {AGENT_SLOTS.map((slot) => {
          const a = byName.get(slot.name);
          const stateClass = a
            ? STATE_BADGE[a.state] ?? "border-border bg-bg text-text-dim"
            : "border-border bg-bg text-text-dim";
          const heatPct = a ? (a.heat_allocation * 100).toFixed(0) : "—";
          const coh = a?.coherence == null ? "—" : a.coherence.toFixed(2);
          const open = a ? a.n_open_positions : 0;
          const last = a?.last_eval_ts ? fmtRelative(a.last_eval_ts) : "—";
          return (
            <li key={slot.name} className="px-3 py-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="text-secondary">
                    {ASSET_ICON[slot.asset_class] ?? "•"}
                  </span>
                  <span className="font-mono text-[11px] font-semibold text-text">
                    {SLOT_LABEL[slot.name] ?? slot.name}
                  </span>
                  <span className="truncate font-mono text-[10px] text-text-dim">
                    {ASSET_LABEL[slot.asset_class] ?? slot.asset_class}
                  </span>
                </div>
                <span
                  className={`rounded border px-1 py-0 font-mono text-[9px] uppercase tracking-wider ${stateClass}`}
                >
                  {a?.state ?? "off"}
                </span>
              </div>
              <div className="mt-1 grid grid-cols-4 gap-1 font-mono text-[10px]">
                <Stat label="heat" value={a ? `${heatPct}%` : "—"} />
                <Stat label="coh" value={coh} />
                <Stat label="open" value={`${open}`} />
                <Stat label="eval" value={last} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col leading-tight">
      <span className="text-[8px] uppercase tracking-wider text-text-dim">{label}</span>
      <span className="text-text">{value}</span>
    </div>
  );
}
