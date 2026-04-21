"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

function fmt(n: number, d = 2) {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

export function PositionsTable() {
  const q = useQuery({ queryKey: ["positions"], queryFn: api.positions });
  const rows = q.data ?? [];

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">positions</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wider text-muted">
            <tr>
              <th className="px-4 py-2">symbol</th>
              <th className="px-4 py-2">qty</th>
              <th className="px-4 py-2">avg entry</th>
              <th className="px-4 py-2">last</th>
              <th className="px-4 py-2">mkt value</th>
              <th className="px-4 py-2">unrealized P&L</th>
              <th className="px-4 py-2">side</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-muted">
                  no open positions
                </td>
              </tr>
            )}
            {rows.map((p) => {
              const upl = p.unrealized_pl;
              return (
                <tr key={p.symbol} className="border-t border-border font-mono">
                  <td className="px-4 py-2 font-semibold">{p.symbol}</td>
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
