"use client";
import { api } from "@/lib/api";
import type { LivePosition, Position } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";

function fmt(n: number, d = 2) {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

/**
 * Live positions: prefers /api/positions/live (per-agent attribution); falls
 * back to /api/positions for the v1 broker-snapshot view.
 */
export function LivePositionsTable() {
  const live = useQuery({ queryKey: ["positions-live"], queryFn: api.livePositions });
  const fallback = useQuery({
    queryKey: ["positions"],
    queryFn: api.positions,
    enabled: !live.data || live.data.length === 0,
  });

  const rows: LivePosition[] | Position[] =
    (live.data && live.data.length > 0 ? live.data : fallback.data) ?? [];

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">live positions</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wider text-muted">
            <tr>
              <th className="px-4 py-2">symbol</th>
              <th className="px-4 py-2">agent</th>
              <th className="px-4 py-2">strategy</th>
              <th className="px-4 py-2">qty</th>
              <th className="px-4 py-2">avg entry</th>
              <th className="px-4 py-2">live mark</th>
              <th className="px-4 py-2">mkt value</th>
              <th className="px-4 py-2">unrealized P&L</th>
              <th className="px-4 py-2">side</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-muted">
                  no open positions
                </td>
              </tr>
            )}
            {rows.map((p) => {
              const lp = p as LivePosition;
              const upl = p.unrealized_pl;
              return (
                <tr key={`${p.symbol}-${lp.agent ?? ""}`} className="border-t border-border font-mono">
                  <td className="px-4 py-2 font-semibold">{p.symbol}</td>
                  <td className="px-4 py-2 text-xs text-muted">{lp.agent ?? "—"}</td>
                  <td className="px-4 py-2 text-xs text-muted">{lp.strategy ?? "—"}</td>
                  <td className="px-4 py-2">{p.qty}</td>
                  <td className="px-4 py-2">${fmt(p.avg_entry_price)}</td>
                  <td className="px-4 py-2">${fmt(p.current_price)}</td>
                  <td className="px-4 py-2">${fmt(p.market_value)}</td>
                  <td
                    className={`px-4 py-2 ${upl >= 0 ? "text-accent" : "text-danger"}`}
                  >
                    {upl >= 0 ? "+" : ""}${fmt(upl)} ({fmt(p.unrealized_plpc * 100)}%)
                  </td>
                  <td className="px-4 py-2 uppercase text-muted">{p.side}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
