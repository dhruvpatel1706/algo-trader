"use client";
import { api } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { BotControl } from "./BotControl";
import { KillSwitch } from "./KillSwitch";

function fmt(n?: number, d = 2) {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

function fmtUptime(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h${m.toString().padStart(2, "0")}m`;
  if (m > 0) return `${m}m${sec.toString().padStart(2, "0")}s`;
  return `${sec}s`;
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
  const agentsQ = useQuery({
    queryKey: ["agents-topbar"],
    queryFn: () => api.agents(),
    refetchInterval: 15_000,
  });
  const positionsQ = useQuery({
    queryKey: ["positions-topbar"],
    queryFn: () => api.positions(),
    refetchInterval: 30_000,
  });
  const pathname = usePathname();

  // Session uptime — capture mount epoch once, tick every second.
  const mountedAtRef = useRef<number>(0);
  if (mountedAtRef.current === 0) mountedAtRef.current = Date.now();
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const p = portfolio.data;
  const dayUsd = p?.day_change_usd;
  const dayPct = p?.day_change_pct;
  const haltState = halt.data?.halted;

  const dayClass =
    dayUsd === undefined
      ? "text-muted"
      : dayUsd >= 0
        ? "text-success"
        : "text-danger";

  // Aggregate open positions across agents (preferred), fall back to broker positions.
  const agents = agentsQ.data ?? [];
  const openFromAgents = agents.reduce(
    (acc, a) => acc + (typeof a.n_open_positions === "number" ? a.n_open_positions : 0),
    0,
  );
  const brokerPositions = positionsQ.data ?? [];
  const nOpen = openFromAgents > 0 ? openFromAgents : brokerPositions.length;

  // Most recent last_eval_ts across all agents.
  const lastEval = agents
    .map((a) => a.last_eval_ts)
    .filter((x): x is string => Boolean(x))
    .sort()
    .at(-1);

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-surface shadow-sm">
      <div className="flex items-center gap-4 px-6 py-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-zinc-200">algo-trader</span>
          <span className="rounded-full bg-bg px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted">
            paper
          </span>
        </div>

        <div className="flex flex-1 flex-wrap items-center gap-x-5 gap-y-1 text-xs">
          <Metric label="equity" value={p ? `$${fmt(p.equity)}` : "—"} />
          <Metric label="cash" value={p ? `$${fmt(p.cash)}` : "—"} />
          <Metric label="b.power" value={p ? `$${fmt(p.buying_power)}` : "—"} />
          <Metric
            label="day P&L"
            value={
              dayUsd === undefined
                ? "—"
                : `${dayUsd >= 0 ? "+" : ""}$${fmt(dayUsd)} (${fmt((dayPct ?? 0) * 100, 2)}%)`
            }
            className={dayClass}
          />
          <Metric label="positions" value={`${nOpen}`} />
          <Metric label="uptime" value={fmtUptime(now - mountedAtRef.current)} />
          <Metric
            label="last eval"
            value={lastEval ? fmtRelative(lastEval, new Date(now)) : "—"}
          />
        </div>

        <div className="flex items-center gap-2">
          <BotControl />
          <span
            className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
              haltState ? "bg-danger/20 text-danger" : "bg-success/20 text-success"
            }`}
          >
            {haltState ? "HALTED" : "ARMED"}
          </span>
          <KillSwitch />
        </div>
      </div>
      <nav className="flex items-center gap-1 border-t border-border px-6 py-1 text-xs">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname?.startsWith(item.href) ?? false;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`rounded-md px-2.5 py-1 transition-colors ${
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
    <div className="flex items-baseline gap-1.5">
      <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted">{label}</span>
      <span className={`font-mono text-[12px] leading-none ${className}`}>{value}</span>
    </div>
  );
}
