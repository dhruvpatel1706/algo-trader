"use client";
import { api } from "@/lib/api";
import type { LivePosition, Position } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

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
 * Crypto positions are fractional (e.g. 3.99 ETH). Equity positions are whole
 * units. Detect the asset class from the symbol shape so the qty column shows
 * the right precision — without this the user sees "3 ETHUSD" for a 3.99 ETH
 * position and thinks $2K+ went missing.
 */
function fmtQty(qty: number | null | undefined, symbol: string): string {
  if (qty === null || qty === undefined || Number.isNaN(qty)) return "—";
  const isCrypto = /USD$|USDT$|USDC$|BTC$|ETH$/i.test(symbol) || symbol.includes("/");
  const decimals = isCrypto ? 4 : 0;
  return qty.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

/** Human-readable "x s ago" / "x m ago" for the price-freshness chip. */
function fmtAge(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const sec = Math.max(0, Math.floor((nowMs - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ago`;
}

/**
 * MAX_SINGLE_POSITION cap from src/config.py — used here only to color the
 * concentration column. The risk module is the source of truth for actual
 * enforcement; this is purely informational so the user can see breach
 * state at a glance without reading a Watcher INCIDENT file.
 */
const MAX_SINGLE_POSITION_PCT = 10;

function concentrationTone(pctOfEquity: number | null): {
  cls: string;
  label: string;
} {
  if (pctOfEquity === null) return { cls: "text-muted", label: "" };
  if (pctOfEquity >= MAX_SINGLE_POSITION_PCT * 1.5)
    return { cls: "text-danger font-semibold", label: "BREACH" };
  if (pctOfEquity >= MAX_SINGLE_POSITION_PCT)
    return { cls: "text-warn font-semibold", label: "AT CAP" };
  if (pctOfEquity >= MAX_SINGLE_POSITION_PCT * 0.75)
    return { cls: "text-warn", label: "near cap" };
  return { cls: "text-muted", label: "" };
}

/**
 * Live positions: prefers /api/positions/live (per-agent attribution); falls
 * back to /api/positions for the v1 broker-snapshot view.
 */
export function LivePositionsTable() {
  const live = useQuery({
    queryKey: ["positions-live"],
    queryFn: api.livePositions,
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
  });
  const fallback = useQuery({
    queryKey: ["positions"],
    queryFn: api.positions,
    refetchInterval: 5_000,
    enabled: !live.data || live.data.length === 0,
  });
  // Pull portfolio so we can compute % of equity per position. If it's
  // unavailable we just show "—" in the concentration column — the column
  // is informational, never blocking.
  const portfolio = useQuery({
    queryKey: ["portfolio"],
    queryFn: api.portfolio,
    refetchInterval: 5_000,
  });
  const equity =
    portfolio.data && Number.isFinite(portfolio.data.equity)
      ? portfolio.data.equity
      : null;

  // Tick a "now" stamp each second so the "X s ago" mark age updates without
  // waiting for a full refetch — the user can SEE the data is fresh even if
  // the price hasn't moved (Alpaca paper crypto ticks are slow).
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

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
              <th className="px-4 py-2">mark age</th>
              <th className="px-4 py-2">mkt value</th>
              <th
                className="px-4 py-2"
                title="Position notional as % of total equity. Cap is MAX_SINGLE_POSITION (10%); cumulative-cap fix in commit bedc7bd refuses fresh adds beyond this."
              >
                % equity
              </th>
              <th className="px-4 py-2">unrealized P&L</th>
              <th className="px-4 py-2">side</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={11} className="px-4 py-6 text-center text-muted">
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
              // Compute notional from market_value, falling back to qty*mark.
              const notional = isNum(p.market_value)
                ? p.market_value
                : isNum(p.qty) && isNum(p.current_price)
                  ? p.qty * p.current_price
                  : null;
              const pctOfEquity =
                notional !== null && equity && equity > 0
                  ? (notional / equity) * 100
                  : null;
              const tone = concentrationTone(pctOfEquity);
              return (
                <tr key={`${p.symbol}-${lp.agent ?? ""}`} className="border-t border-border font-mono">
                  <td className="px-4 py-2 font-semibold">{p.symbol}</td>
                  <td className="px-4 py-2 text-xs text-muted">{lp.agent ?? "—"}</td>
                  <td className="px-4 py-2 text-xs text-muted">{lp.strategy ?? "—"}</td>
                  <td className="px-4 py-2">{fmtQty(p.qty, p.symbol)}</td>
                  <td className="px-4 py-2">${fmt(p.avg_entry_price)}</td>
                  <td className="px-4 py-2">${fmt(p.current_price)}</td>
                  <td
                    className="px-4 py-2 text-xs text-muted"
                    title={lp.mark_source ?? "—"}
                  >
                    {fmtAge(lp.mark_as_of, nowMs)}
                  </td>
                  <td className="px-4 py-2">${fmt(p.market_value)}</td>
                  <td className={`px-4 py-2 ${tone.cls}`}>
                    {pctOfEquity === null ? "—" : `${fmt(pctOfEquity, 1)}%`}
                    {tone.label && (
                      <span className="ml-2 text-[10px] uppercase tracking-wider">
                        {tone.label}
                      </span>
                    )}
                  </td>
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
