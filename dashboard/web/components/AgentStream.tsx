"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

export function AgentStream() {
  const q = useQuery({ queryKey: ["agent-events"], queryFn: () => api.agentEvents() });
  const rows = q.data ?? [];
  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">agent activity</h2>
      </div>
      <ul className="max-h-[280px] divide-y divide-border overflow-y-auto text-sm">
        {rows.length === 0 && (
          <li className="px-4 py-6 text-center text-muted">no agent events yet</li>
        )}
        {rows.map((e, i) => (
          <li key={i} className="flex items-center gap-4 px-4 py-2 font-mono text-xs">
            <span className="w-44 truncate text-muted">{String(e.ts ?? "—")}</span>
            <span className="w-32 truncate text-zinc-200">{String(e.agent ?? "—")}</span>
            <span className="flex-1 truncate text-muted">{String(e.action ?? "")}</span>
            <span className="w-16 text-right text-muted">
              {e.duration_ms ? `${e.duration_ms} ms` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
