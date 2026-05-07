"use client";

import { safeFetch } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

type RoleState = {
  last_update_iso: string | null;
  lock_held: boolean;
  lock_pid: number | null;
  latest_verdict_path: string | null;
  brief_excerpt: string | null;
  staleness: "fresh" | "warn" | "stale";
};

type OrchestratorStateResponse = {
  roles: Record<string, RoleState>;
  as_of: string;
};

const ROLE_ORDER = ["watcher", "researcher", "backtester", "improver", "operator"] as const;

const ROLE_LABEL: Record<string, string> = {
  watcher: "watcher",
  researcher: "researcher",
  backtester: "backtester",
  improver: "improver",
  operator: "operator",
};

const CADENCE_LABEL: Record<string, string> = {
  watcher: "15 min",
  researcher: "4 h",
  backtester: "24 h",
  improver: "7 d",
  operator: "4 h",
};

const STALENESS_CLS: Record<string, string> = {
  fresh: "border-success/30 bg-success/5 text-success",
  warn: "border-warn/30 bg-warn/5 text-warn",
  stale: "border-danger/30 bg-danger/5 text-danger",
};

function fmtIso(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().slice(0, 19).replace("T", " ") + "z";
  } catch {
    return iso.slice(0, 19);
  }
}

function briefSnippet(excerpt: string | null, last_update: string | null): string {
  if (excerpt) return excerpt.slice(0, 90).replace(/\n+/g, " ");
  if (last_update) return `updated ${fmtIso(last_update)}`;
  return "no data yet";
}

export function OrchestratorPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ["orchestrator-state"],
    queryFn: () =>
      safeFetch<OrchestratorStateResponse | null>("/api/orchestrator/state", null),
    refetchInterval: 30_000,
  });

  const roles = data?.roles ?? {};

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex items-center justify-between border-b border-border px-4 py-2">
        <h2 className="text-sm font-semibold tracking-wide text-text">
          orchestrator sessions
        </h2>
        {data?.as_of ? (
          <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
            as of {fmtIso(data.as_of).slice(11, 19)}z
          </span>
        ) : (
          <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
            30 s poll
          </span>
        )}
      </header>

      {isLoading ? (
        <div className="px-5 py-6 text-center font-mono text-[11px] text-muted">loading…</div>
      ) : (
        <div className="divide-y divide-border">
          {ROLE_ORDER.map((role) => {
            const state = roles[role];
            if (!state) return null;
            return (
              <div key={role} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
                {/* Role name + cadence */}
                <span className="w-20 shrink-0 font-mono text-[11px] uppercase tracking-wider text-text-dim">
                  {ROLE_LABEL[role]}
                </span>
                <span className="font-mono text-[9px] text-text-dim">
                  /{CADENCE_LABEL[role]}
                </span>

                {/* Staleness chip */}
                <span
                  className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${STALENESS_CLS[state.staleness] ?? STALENESS_CLS.stale}`}
                >
                  {state.staleness}
                </span>

                {/* Lock indicator */}
                {state.lock_held && (
                  <span className="rounded border border-info/30 bg-info/5 px-1.5 py-0.5 font-mono text-[10px] text-info">
                    locked · pid {state.lock_pid}
                  </span>
                )}

                {/* Brief snippet / last-update */}
                <span className="ml-auto max-w-[360px] truncate font-mono text-[10px] text-text-dim">
                  {briefSnippet(state.brief_excerpt, state.last_update_iso)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
