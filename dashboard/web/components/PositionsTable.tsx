"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

/**
 * Defensive numeric formatter. Broker proxy can return null on freshly-opened
 * positions or partial fills before P&L has been computed; rendering "—"
 * beats crashing the dashboard.
 */
function fmt(n: number | null | undefined, d = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

function isNum(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n);
}

function fmtQty(qty: number | null | undefined, symbol: string): string {
  if (qty === null || qty === undefined || Number.isNaN(qty)) return "—";
  const isCrypto = /USD$|USDT$|USDC$|BTC$|ETH$/i.test(symbol) || symbol.includes("/");
  return qty.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: isCrypto ? 4 : 0,
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
              const upl = isNum(p.unrealized_pl) ? p.unrealized_pl : 0;
              const uplPct = isNum(p.unrealized_plpc) ? p.unrealized_plpc * 100 : null;
              return (
                <tr key={p.symbol} className="border-t border-border font-mono">
                  <td className="px-4 py-2 font-semibold">{p.symbol}</td>
                  <td className="px-4 py-2">{fmtQty(p.qty, p.symbol)}</td>
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
