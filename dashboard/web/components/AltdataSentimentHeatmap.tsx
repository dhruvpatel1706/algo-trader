"use client";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

/**
 * Score color mapping. -1 -> red, 0 -> neutral, +1 -> green.
 */
function colorFor(score: number): string {
  const s = Math.max(-1, Math.min(1, score));
  if (s >= 0) {
    const a = (s * 0.7 + 0.1).toFixed(2);
    return `rgba(34, 197, 94, ${a})`;
  }
  const a = (-s * 0.7 + 0.1).toFixed(2);
  return `rgba(239, 68, 68, ${a})`;
}

export function AltdataSentimentHeatmap() {
  const q = useQuery({ queryKey: ["alt-sentiment"], queryFn: () => api.altSentiment() });
  const rows = q.data ?? [];

  const { tickers, dates, grid } = useMemo(() => {
    const ts = new Set<string>();
    const ds = new Set<string>();
    const g = new Map<string, { score: number; volume: number }>();
    for (const c of rows) {
      ts.add(c.ticker);
      ds.add(c.date);
      g.set(`${c.ticker}|${c.date}`, { score: c.score, volume: c.volume });
    }
    return {
      tickers: Array.from(ts).sort(),
      dates: Array.from(ds).sort(),
      grid: g,
    };
  }, [rows]);

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">news sentiment heatmap</h2>
        <p className="text-xs text-muted">
          green = positive, red = negative; opacity tracks score magnitude
        </p>
      </div>
      <div className="overflow-x-auto p-3">
        {rows.length === 0 ? (
          <p className="px-4 py-6 text-center text-muted">no sentiment data yet</p>
        ) : (
          <table className="text-xs">
            <thead>
              <tr>
                <th className="px-2 py-1 text-left text-muted"></th>
                {dates.map((d) => (
                  <th
                    key={d}
                    className="px-1 py-1 text-center font-mono text-[10px] text-muted"
                  >
                    {d.slice(5)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tickers.map((t) => (
                <tr key={t}>
                  <td className="px-2 py-1 font-mono text-zinc-200">{t}</td>
                  {dates.map((d) => {
                    const cell = grid.get(`${t}|${d}`);
                    return (
                      <td key={d} className="p-0.5">
                        <div
                          title={
                            cell
                              ? `${t} ${d}\nscore: ${cell.score.toFixed(2)}\nvol: ${cell.volume}`
                              : "no data"
                          }
                          className="h-6 w-8 rounded"
                          style={{
                            background: cell ? colorFor(cell.score) : "#1f242c",
                          }}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
