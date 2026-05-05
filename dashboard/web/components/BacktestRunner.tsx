"use client";
import { api } from "@/lib/api";
import type { BacktestRun } from "@/lib/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

function fmtPct(n: number) {
  return `${(n * 100).toFixed(2)}%`;
}

export function BacktestRunner() {
  const qc = useQueryClient();
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  const history = useQuery({
    queryKey: ["backtest-history"],
    queryFn: () => api.backtestHistory(),
  });

  const [strategy, setStrategy] = useState("");
  const [symbol, setSymbol] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [latest, setLatest] = useState<BacktestRun | null>(null);

  const run = useMutation({
    mutationFn: () =>
      api.runBacktest({
        strategy,
        symbol: symbol || undefined,
        start,
        end,
      }),
    onSuccess: (data) => {
      if (!data) {
        setError("backtest endpoint returned no data");
        return;
      }
      setLatest(data);
      qc.invalidateQueries({ queryKey: ["backtest-history"] });
    },
    onError: (e) => setError(String(e)),
  });

  const stratList = (strategies.data ?? []).map((s) => s.name);
  const past = history.data ?? [];

  return (
    <div className="space-y-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          if (!strategy || !start || !end) {
            setError("strategy, start, end are required");
            return;
          }
          run.mutate();
        }}
        className="rounded-lg border border-border bg-surface p-4"
      >
        <h2 className="mb-3 text-sm font-semibold text-zinc-200">run backtest</h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="strategy">
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="w-full rounded-md border border-border bg-bg px-2 py-1 font-mono text-zinc-100"
            >
              <option value="">select…</option>
              {stratList.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>
          <Field label="symbol (optional)">
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="AAPL"
              className="w-full rounded-md border border-border bg-bg px-2 py-1 font-mono text-zinc-100"
            />
          </Field>
          <Field label="start">
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="w-full rounded-md border border-border bg-bg px-2 py-1 font-mono text-zinc-100"
            />
          </Field>
          <Field label="end">
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="w-full rounded-md border border-border bg-bg px-2 py-1 font-mono text-zinc-100"
            />
          </Field>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button
            type="submit"
            disabled={run.isPending}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-bg disabled:opacity-50"
          >
            {run.isPending ? "running…" : "run backtest"}
          </button>
          {error && <span className="text-sm text-danger">{error}</span>}
        </div>
      </form>

      {latest && (
        <div className="rounded-lg border border-border bg-surface p-4">
          <h3 className="mb-2 text-sm font-semibold text-zinc-200">latest run</h3>
          <RunRow run={latest} highlight />
        </div>
      )}

      <div className="rounded-lg border border-border bg-surface">
        <div className="border-b border-border px-4 py-3">
          <h3 className="text-sm font-semibold text-zinc-200">past runs</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-muted">
              <tr>
                <th className="px-4 py-2">id</th>
                <th className="px-4 py-2">strategy</th>
                <th className="px-4 py-2">period</th>
                <th className="px-4 py-2 text-right">return</th>
                <th className="px-4 py-2 text-right">sharpe</th>
                <th className="px-4 py-2 text-right">max DD</th>
                <th className="px-4 py-2 text-right">trades</th>
              </tr>
            </thead>
            <tbody>
              {past.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-6 text-center text-muted">
                    no past backtests yet
                  </td>
                </tr>
              )}
              {past.map((b) => {
                const id = b.run_id ?? b.id ?? "—";
                const totalReturn = b.total_return;
                const maxDd = b.max_drawdown ?? b.max_dd ?? null;
                return (
                  <tr key={id} className="border-t border-border font-mono">
                    <td className="px-4 py-2 text-xs text-muted">{id}</td>
                    <td className="px-4 py-2 text-zinc-100">{b.strategy ?? "—"}</td>
                    <td className="px-4 py-2 text-xs text-muted">
                      {b.start ?? "—"} → {b.end ?? "—"}
                    </td>
                    <td
                      className={`px-4 py-2 text-right ${
                        totalReturn != null && totalReturn >= 0
                          ? "text-accent"
                          : totalReturn != null
                            ? "text-danger"
                            : "text-muted"
                      }`}
                    >
                      {totalReturn != null ? fmtPct(totalReturn) : "—"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {b.sharpe != null ? b.sharpe.toFixed(2) : "—"}
                    </td>
                    <td className="px-4 py-2 text-right text-danger">
                      {maxDd != null ? fmtPct(-Math.abs(maxDd)) : "—"}
                    </td>
                    <td className="px-4 py-2 text-right">{b.n_trades ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function RunRow({ run, highlight }: { run: BacktestRun; highlight?: boolean }) {
  // Optional fields are only populated when this row came from `runBacktest()`
  // (POST result). When listed via `/api/backtest/history` the slim shape lacks
  // them, so we render `—` rather than crashing.
  const strategy = run.strategy ?? "—";
  const totalReturn = run.total_return;
  const sharpe = run.sharpe;
  const maxDd = run.max_drawdown ?? run.max_dd ?? null;
  const nTrades = run.n_trades;
  return (
    <div
      className={`grid grid-cols-2 gap-3 text-sm md:grid-cols-5 ${
        highlight ? "rounded-md bg-bg p-3" : ""
      }`}
    >
      <Stat label="strategy" value={strategy} />
      <Stat
        label="return"
        value={totalReturn != null ? fmtPct(totalReturn) : "—"}
        className={
          totalReturn != null && totalReturn >= 0
            ? "text-accent"
            : totalReturn != null
              ? "text-danger"
              : ""
        }
      />
      <Stat label="sharpe" value={sharpe != null ? sharpe.toFixed(2) : "—"} />
      <Stat
        label="max DD"
        value={maxDd != null ? fmtPct(-Math.abs(maxDd)) : "—"}
        className="text-danger"
      />
      <Stat label="trades" value={nTrades != null ? String(nTrades) : "—"} />
    </div>
  );
}

function Stat({
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
      <span className={`font-mono text-zinc-100 ${className}`}>{value}</span>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-muted">
      <span>{label}</span>
      {children}
    </label>
  );
}
