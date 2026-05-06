/**
 * Number / currency / time formatters used across the dashboard.
 * All numbers render in tabular-nums for stable column alignment.
 */

export function fmtUsd(value: number | null | undefined, opts: { compact?: boolean } = {}): string {
  if (value == null || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (opts.compact && abs >= 1000) {
    if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
    if (abs >= 10_000) return `${(value / 1_000).toFixed(1)}k`;
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function fmtPct(value: number | null | undefined, decimals = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(decimals)}%`;
}

export function fmtPctSigned(value: number | null | undefined, decimals = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "" : "";
  return `${sign}${(value * 100).toFixed(decimals)}%`;
}

export function fmtUsdSigned(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  const abs = Math.abs(value);
  return `${sign}${new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(abs)}`;
}

export function fmtNum(value: number | null | undefined, decimals = 0): string {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleDateString("en-US", { month: "short", day: "2-digit" })} ${d.toLocaleTimeString(
    "en-US",
    { hour: "2-digit", minute: "2-digit", hour12: false },
  )}`;
}

export function fmtRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffSec = Math.floor((now.getTime() - d.getTime()) / 1000);
  if (diffSec < 0) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86_400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86_400)}d ago`;
}

export function pnlColorClass(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "text-text-dim";
  if (value > 0) return "text-success";
  if (value < 0) return "text-danger";
  return "text-text-dim";
}

export function pnlGlowClass(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "";
  if (value > 0) return "glow-up";
  if (value < 0) return "glow-down";
  return "";
}
