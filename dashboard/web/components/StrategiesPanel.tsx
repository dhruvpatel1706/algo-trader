"use client";
import { api } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export function StrategiesPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  const pause = useMutation({
    mutationFn: (name: string) => api.pause(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
  });
  const resume = useMutation({
    mutationFn: (name: string) => api.resume(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
  });
  const rows = q.data ?? [];

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">strategies</h2>
      </div>
      <ul className="divide-y divide-border text-sm">
        {rows.map((s) => (
          <li key={s.name} className="flex items-center justify-between px-4 py-3">
            <div className="flex items-center gap-3">
              <span className="font-mono">{s.name}</span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs ${
                  s.enabled ? "bg-accent/20 text-accent" : "bg-zinc-700/50 text-muted"
                }`}
              >
                {s.enabled ? "enabled" : "paused"}
              </span>
            </div>
            <button
              onClick={() => (s.enabled ? pause.mutate(s.name) : resume.mutate(s.name))}
              className="rounded-md border border-border px-3 py-1 text-xs hover:bg-border"
            >
              {s.enabled ? "pause" : "resume"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
