"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

function fmtUsd(n: number | null | undefined) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

export function AltdataInsiderPanel() {
  const q = useQuery({ queryKey: ["alt-insider"], queryFn: () => api.altInsider() });
  const rows = q.data ?? [];

  // count how many trades share the same cluster_id to highlight clusters
  const clusterCount = new Map<string, number>();
  rows.forEach((r) => {
    if (r.cluster_id) {
      clusterCount.set(r.cluster_id, (clusterCount.get(r.cluster_id) ?? 0) + 1);
    }
  });

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">SEC Form 4 — insider trades</h2>
        <p className="text-xs text-muted">
          highlighted rows belong to clusters of recent buys
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wider text-muted">
            <tr>
              <th className="px-4 py-2">date</th>
              <th className="px-4 py-2">ticker</th>
              <th className="px-4 py-2">insider</th>
              <th className="px-4 py-2">title</th>
              <th className="px-4 py-2">side</th>
              <th className="px-4 py-2 text-right">shares</th>
              <th className="px-4 py-2 text-right">value</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-muted">
                  no insider data yet
                </td>
              </tr>
            )}
            {rows.map((r, i) => {
              const isCluster =
                r.cluster_id && (clusterCount.get(r.cluster_id) ?? 0) >= 2;
              return (
                <tr
                  key={i}
                  className={`border-t border-border font-mono ${
                    isCluster ? "bg-accent/5" : ""
                  }`}
                >
                  <td className="px-4 py-2 text-xs text-muted">{r.ts}</td>
                  <td className="px-4 py-2 font-semibold">{r.ticker}</td>
                  <td className="px-4 py-2 text-zinc-200">{r.insider}</td>
                  <td className="px-4 py-2 text-xs text-muted">{r.title ?? "—"}</td>
                  <td className="px-4 py-2">
                    <span
                      className={`rounded px-2 py-0.5 text-[10px] uppercase ${
                        r.side === "buy"
                          ? "bg-accent/20 text-accent"
                          : "bg-danger/20 text-danger"
                      }`}
                    >
                      {r.side}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    {r.shares !== null && r.shares !== undefined ? r.shares.toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-2 text-right">${fmtUsd(r.value_usd)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
