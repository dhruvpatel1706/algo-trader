"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

function fmtUsd(n: number) {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

function shortAddr(addr: string) {
  if (addr.length <= 12) return addr;
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

export function AltdataWalletsPanel() {
  const q = useQuery({ queryKey: ["alt-wallets"], queryFn: () => api.altWallets() });
  const rows = q.data ?? [];

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">smart-money wallets</h2>
        <p className="text-xs text-muted">recent on-chain trades by tracked wallets</p>
      </div>
      <ul className="max-h-[420px] divide-y divide-border overflow-y-auto text-sm">
        {rows.length === 0 && (
          <li className="px-4 py-6 text-center text-muted">no wallet activity yet</li>
        )}
        {rows.map((w, i) => (
          <li key={i} className="flex items-center gap-3 px-4 py-2 font-mono text-xs">
            <span className="w-44 truncate text-muted">{w.ts}</span>
            <span className="w-32 truncate text-zinc-200">
              {w.label ?? shortAddr(w.wallet)}
            </span>
            <span className="w-16 truncate text-muted">{w.chain}</span>
            <span className="w-20 font-semibold text-zinc-100">{w.token}</span>
            <span
              className={`w-12 rounded px-1 py-0.5 text-center text-[10px] uppercase ${
                w.side === "buy"
                  ? "bg-accent/20 text-accent"
                  : "bg-danger/20 text-danger"
              }`}
            >
              {w.side}
            </span>
            <span className="ml-auto text-right">${fmtUsd(w.size_usd)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
