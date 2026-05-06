"use client";

// A small toolbar button that downloads /api/trades/export.csv as a file.
// No external deps — uses fetch + Blob URL + a synthetic <a> click.
//
// The component is stateless aside from the inflight flag. Pass `from` /
// `to` / `strategy` to pre-filter the export; leave them empty for the
// default 30-day window the backend computes itself.

import { useState } from "react";

type Props = {
  /** ISO date string (YYYY-MM-DD), inclusive lower bound. Optional. */
  from?: string;
  /** ISO date string (YYYY-MM-DD), inclusive upper bound. Optional. */
  to?: string;
  /** Exact-match strategy filter. Optional. */
  strategy?: string;
  /** Tailwind/extra classes appended to the default button styling. */
  className?: string;
};

function buildUrl(from?: string, to?: string, strategy?: string): string {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  if (strategy) params.set("strategy", strategy);
  const qs = params.toString();
  return qs ? `/api/trades/export.csv?${qs}` : "/api/trades/export.csv";
}

function suggestedFilename(from?: string, to?: string): string {
  if (from && to) return `trades_${from}_${to}.csv`;
  if (from) return `trades_${from}.csv`;
  if (to) return `trades_${to}.csv`;
  return "trades.csv";
}

export function TradeExportButton({ from, to, strategy, className }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(buildUrl(from, to, strategy), { cache: "no-store" });
      if (!r.ok) throw new Error(`export failed: ${r.status}`);
      const blob = await r.blob();
      // Prefer a Content-Disposition filename if the server gave one; fall
      // back to a sensible default. Some browsers ignore the inline filename
      // when downloading a Blob URL, hence the explicit `download` attr.
      const cd = r.headers.get("content-disposition") ?? "";
      const match = cd.match(/filename="?([^"]+)"?/i);
      const name = match?.[1] ?? suggestedFilename(from, to);

      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Defer revocation slightly so the browser has time to start the
      // download. 1s is generous on every modern browser.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "download failed");
    } finally {
      setBusy(false);
    }
  }

  // Style vocabulary mirrors KillSwitch / BotControl: small monospaced
  // pill-shaped button, surface-level neutrals, success accent for "active"
  // intent. Errors are surfaced inline below the button.
  const baseCls =
    "rounded-md border border-border bg-bg px-2.5 py-1 font-mono text-[10px] " +
    "font-semibold uppercase tracking-wider text-text hover:bg-success/10 " +
    "hover:text-success disabled:opacity-50";

  return (
    <div className="relative flex flex-col items-end gap-0.5">
      <button
        type="button"
        onClick={handleDownload}
        disabled={busy}
        className={className ? `${baseCls} ${className}` : baseCls}
        aria-label="download all trades as CSV"
        title="download all trades as CSV"
      >
        {busy ? "Downloading…" : "Export CSV"}
      </button>
      {error && (
        <span
          role="status"
          aria-live="polite"
          className="font-mono text-[9px] text-danger"
        >
          {error}
        </span>
      )}
    </div>
  );
}
