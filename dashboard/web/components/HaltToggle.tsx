"use client";
import { api } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export function HaltToggle({ strategy }: { strategy: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const strategies = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  const current = (strategies.data ?? []).find((s) => s.name === strategy);
  const enabled = current?.enabled ?? true;

  const pause = useMutation({
    mutationFn: () => api.pause(strategy),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
    onError: (e) => setError(String(e)),
  });
  const resume = useMutation({
    mutationFn: () => api.resume(strategy),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
    onError: (e) => setError(String(e)),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const handle = () => {
    setError(null);
    if (enabled) pause.mutate();
    else resume.mutate();
    setOpen(false);
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className={`rounded-md px-3 py-1.5 text-xs font-semibold ${
          enabled
            ? "bg-warn/20 text-warn hover:bg-warn/30"
            : "bg-accent/20 text-accent hover:bg-accent/30"
        }`}
      >
        {enabled ? "halt strategy" : "resume strategy"}
      </button>
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-md rounded-lg border border-border bg-surface p-6 shadow-2xl">
            <h2 className="mb-2 text-lg font-bold text-zinc-100">
              {enabled ? "Halt" : "Resume"} {strategy}?
            </h2>
            <p className="mb-4 text-sm text-muted">
              {enabled
                ? "Pauses signal generation for this strategy. Open positions are not affected."
                : "Resumes signal generation for this strategy."}
            </p>
            {error && <p className="mb-2 text-sm text-danger">{error}</p>}
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setOpen(false)}
                className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-border"
              >
                Cancel
              </button>
              <button
                onClick={handle}
                disabled={pause.isPending || resume.isPending}
                className={`rounded-md px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50 ${
                  enabled ? "bg-warn" : "bg-accent"
                }`}
              >
                {enabled ? "Halt" : "Resume"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
