"use client";
import { api } from "@/lib/api";
import type { LivePosition, Position } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";

/**
 * Defensive numeric formatter.
 *
 * The TypeScript types claim these fields are `number`, but the broker proxy
 * returns raw Alpaca payloads where any of avg_entry_price / current_price /
 * market_value / unrealized_pl / unrealized_plpc can be null or absent —
 * especially for freshly-opened positions Alpaca hasn't yet computed P&L on,
 * or crypto positions on the paper API where some fields lag the fill by
 * a beat or two. Returning a placeholder beats crashing the entire UI.
 */
function fmt(n: number | null | undefined, d = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

/** True for finite, non-null, non-NaN numbers. */
function isNum(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n);
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
              const lp = p as LivePosition & { strategy?: string };
              // Treat partial broker payloads as 0 for the sign-coloring decision
              // — better to show black neutral than crash on a brand-new position
              // that hasn't reported P&L yet.
              const upl = isNum(p.unrealized_pl) ? p.unrealized_pl : 0;
              const uplPct = isNum(p.unrealized_plpc) ? p.unrealized_plpc * 100 : null;
              return (
                <tr key={`${p.symbol}-${lp.agent ?? ""}`} className="border-t border-border font-mono">
                  <td className="px-4 py-2 font-semibold">{p.symbol}</td>
                  <td className="px-4 py-2 text-xs text-muted">{lp.agent ?? "—"}</td>
                  <td className="px-4 py-2 text-xs text-muted">{lp.strategy ?? "—"}</td>
                  <td className="px-4 py-2">{p.qty ?? "—"}</td>
                  <td className="px-4 py-2">${fmt(p.avg_entry_price)}</td>
                  <td className="px-4 py-2">${fmt(p.current_price)}</td>
                  <td className="px-4 py-2">${fmt(p.market_value)}</td>
                  <td
                    className={`px-4 py-2 ${upl > 0 ? "text-accent" : upl < 0 ? "text-danger" : "text-muted"}`}
                  >
                    {isNum(p.unrealized_pl)
                      ? `${p.unrealized_pl >= 0 ? "+" : ""}$${fmt(p.unrealized_pl)}`
                      : "—"}
                    {uplPct !== null ? ` (${fmt(uplPct)}%)` : ""}
                  </td>
                  <td className="px-4 py-2 uppercase text-muted">{p.side ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
