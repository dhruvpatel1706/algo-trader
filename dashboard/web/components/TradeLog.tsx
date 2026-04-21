"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

export function TradeLog() {
  const q = useQuery({ queryKey: ["trades"], queryFn: () => api.trades() });
  const rows = (q.data ?? []).slice(-50).reverse();

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">recent trade events (journal)</h2>
      </div>
      <ul className="max-h-[280px] divide-y divide-border overflow-y-auto text-sm">
        {rows.length === 0 && (
          <li className="px-4 py-6 text-center text-muted">no trade events in journal</li>
        )}
        {rows.map((t, i) => (
          <li key={i} className="flex items-center gap-4 px-4 py-2 font-mono">
            <span className="w-44 truncate text-xs text-muted">{t.ts ?? "—"}</span>
            <span className="w-24 rounded bg-bg px-2 py-0.5 text-xs uppercase text-muted">
              {t.event}
            </span>
            <span className="w-16 font-semibold">{t.symbol ?? t.subject ?? "—"}</span>
            <span className="w-16 text-right">{t.qty ?? "—"}</span>
            <span className="w-12 uppercase text-muted">{t.side ?? "—"}</span>
            <span className="ml-auto text-xs text-muted">{t.status ?? ""}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
