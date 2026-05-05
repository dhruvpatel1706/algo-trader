"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { KillSwitch } from "./KillSwitch";

function fmt(n?: number, d = 2) {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

const NAV: { label: string; href: string }[] = [
  { label: "Portfolio", href: "/" },
  { label: "Agents", href: "/agents" },
  { label: "Strategies", href: "/strategies" },
  { label: "Signals", href: "/signals" },
  { label: "Alt-data", href: "/altdata" },
  { label: "Backtests", href: "/backtests" },
];

export function TopBar() {
  const portfolio = useQuery({ queryKey: ["portfolio"], queryFn: api.portfolio });
  const halt = useQuery({ queryKey: ["halt"], queryFn: api.halt });
  const pathname = usePathname();

  const p = portfolio.data;
  const dayUsd = p?.day_change_usd;
  const dayPct = p?.day_change_pct;
  const haltState = halt.data?.halted;

  const dayClass =
    dayUsd === undefined
      ? "text-muted"
      : dayUsd >= 0
        ? "text-accent"
        : "text-danger";

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-surface shadow-sm">
      <div className="flex items-center justify-between gap-6 px-6 py-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-zinc-200">algo-trader</span>
          <span className="rounded-full bg-bg px-2 py-0.5 text-xs text-muted">paper</span>
        </div>

        <div className="flex flex-1 items-center gap-8 text-sm">
          <Metric label="equity" value={p ? `$${fmt(p.equity)}` : "—"} />
          <Metric label="cash" value={p ? `$${fmt(p.cash)}` : "—"} />
          <Metric label="buying power" value={p ? `$${fmt(p.buying_power)}` : "—"} />
          <Metric
            label="day P&L"
            value={
              dayUsd === undefined
                ? "—"
                : `${dayUsd >= 0 ? "+" : ""}$${fmt(dayUsd)} (${fmt((dayPct ?? 0) * 100, 2)}%)`
            }
            className={dayClass}
          />
        </div>

        <div className="flex items-center gap-3">
          <span
            className={`rounded-full px-2 py-1 text-xs font-semibold ${
              haltState ? "bg-danger/20 text-danger" : "bg-accent/20 text-accent"
            }`}
          >
            {haltState ? "HALTED" : "ARMED"}
          </span>
          <KillSwitch />
        </div>
      </div>
      <nav className="flex items-center gap-1 border-t border-border px-6 py-1.5 text-xs">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname?.startsWith(item.href) ?? false;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`rounded-md px-3 py-1.5 transition-colors ${
                active
                  ? "bg-bg text-zinc-100"
                  : "text-muted hover:bg-bg hover:text-zinc-200"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}

function Metric({
  label,
  value,
  className = "",
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-muted">{label}</span>
      <span className={`text-sm font-mono ${className}`}>{value}</span>
    </div>
  );
}
