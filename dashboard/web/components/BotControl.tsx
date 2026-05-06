"use client";

import { api } from "@/lib/api";
import type { BotStatus } from "@/lib/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

const STATE_LABEL: Record<BotStatus["state"], string> = {
  running: "RUNNING",
  stopped: "STOPPED",
  crashed: "CRASHED",
};

const STATE_BADGE: Record<BotStatus["state"], string> = {
  running: "bg-success/20 text-success",
  stopped: "bg-zinc-700/50 text-muted",
  crashed: "bg-danger/20 text-danger",
};

function fmtUptime(sec: number | null | undefined): string {
  if (!sec || sec < 1) return "—";
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h${m.toString().padStart(2, "0")}m`;
  if (m > 0) return `${m}m${r.toString().padStart(2, "0")}s`;
  return `${r}s`;
}

export function BotControl() {
  const qc = useQueryClient();
  const [logsOpen, setLogsOpen] = useState(false);

  const statusQ = useQuery<BotStatus>({
    queryKey: ["bot-status"],
    queryFn: api.botStatus,
    refetchInterval: (q) => (q.state.data?.state === "running" ? 3_000 : 5_000),
  });

  const start = useMutation({
    mutationFn: api.botStart,
    onSuccess: (data) => qc.setQueryData(["bot-status"], data),
  });
  const stop = useMutation({
    mutationFn: api.botStop,
    onSuccess: (data) => qc.setQueryData(["bot-status"], data),
  });

  const status = statusQ.data;
  const state = status?.state ?? "stopped";
  const isRunning = state === "running";
  const busy = start.isPending || stop.isPending;

  return (
    <div className="relative flex items-center gap-1.5">
      <span
        className={`rounded-full px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${STATE_BADGE[state]}`}
        title={
          status?.exit_code != null
            ? `last exit code: ${status.exit_code}`
            : undefined
        }
      >
        BOT {STATE_LABEL[state]}
      </span>
      {isRunning ? (
        <button
          onClick={() => stop.mutate()}
          disabled={busy}
          className="rounded-md border border-border bg-bg px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-text hover:bg-warn/10 hover:text-warn disabled:opacity-50"
          aria-label="stop the trading bot"
        >
          {stop.isPending ? "stopping…" : "Stop"}
        </button>
      ) : (
        <button
          onClick={() => start.mutate()}
          disabled={busy}
          className="rounded-md border border-success/40 bg-success/10 px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-success hover:bg-success/20 disabled:opacity-50"
          aria-label="start the trading bot"
        >
          {start.isPending ? "starting…" : "Start"}
        </button>
      )}
      {status?.uptime_sec != null && isRunning && (
        <span className="font-mono text-[9px] text-muted" title="uptime">
          {fmtUptime(status.uptime_sec)}
        </span>
      )}
      <button
        onClick={() => setLogsOpen((v) => !v)}
        className="rounded-md border border-border bg-bg px-1.5 py-1 font-mono text-[10px] uppercase text-muted hover:text-text"
        title="toggle log tail"
        aria-label="toggle log tail"
        aria-expanded={logsOpen}
      >
        ▾
      </button>
      {logsOpen && (
        <div className="absolute right-0 top-full z-50 mt-1.5 max-h-80 w-[640px] overflow-auto rounded-md border border-border bg-bg p-2 shadow-xl">
          <div className="mb-1 flex items-center justify-between font-mono text-[10px] uppercase tracking-wider text-muted">
            <span>runner log (tail · live/runtime/runner.log)</span>
            <button
              onClick={() => setLogsOpen(false)}
              className="text-muted hover:text-text"
              aria-label="close log"
            >
              ✕
            </button>
          </div>
          {status?.log_tail && status.log_tail.length > 0 ? (
            <pre className="whitespace-pre-wrap font-mono text-[10px] leading-snug text-text-dim">
              {status.log_tail.join("\n")}
            </pre>
          ) : (
            <div className="font-mono text-[10px] text-muted">
              no log output yet — press Start to launch the runner
            </div>
          )}
          {status?.exit_code != null && state !== "running" && (
            <div className="mt-1 font-mono text-[10px] text-warn">
              last exit code: {status.exit_code}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
