"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

/** Defensive integer formatter — null/undefined/NaN render as "—". */
function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return n.toLocaleString();
}

function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return `$${n.toFixed(4)}`;
}

export function CostCounter() {
  const q = useQuery({ queryKey: ["costs"], queryFn: api.costs });
  const c = q.data;
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <h2 className="mb-3 text-sm font-semibold text-zinc-200">cost (today)</h2>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Stat label="LLM input" value={fmtInt(c?.llm_input_tokens)} />
        <Stat label="LLM output" value={fmtInt(c?.llm_output_tokens)} />
        <Stat label="API requests" value={fmtInt(c?.api_requests)} />
        <Stat label="USD est." value={fmtUsd(c?.estimated_usd)} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-muted">{label}</span>
      <span className="font-mono text-zinc-100">{value}</span>
    </div>
  );
}
