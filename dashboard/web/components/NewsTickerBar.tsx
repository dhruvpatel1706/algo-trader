"use client";

import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import type { SentimentCell } from "@/lib/types";

/**
 * Bloomberg-style horizontal scrolling ticker. Wires to /api/altdata/sentiment.
 * When the endpoint returns no rows (no Finnhub key, sentiment pipeline idle,
 * or recent run produced no data), the ticker shows a single "no live wire"
 * cell — never fake market headlines.
 */

type TickerItem = {
  key: string;
  ticker: string;
  text: string;
  tone: "pos" | "neg" | "neutral";
};

function toneClass(t: TickerItem["tone"]): string {
  if (t === "pos") return "text-success";
  if (t === "neg") return "text-danger";
  return "text-text-dim";
}

function arrow(t: TickerItem["tone"]): string {
  if (t === "pos") return "▲";
  if (t === "neg") return "▼";
  return "•";
}

function adapt(cells: SentimentCell[]): TickerItem[] {
  return cells.slice(0, 16).map((c, i) => ({
    key: `${c.ticker}-${c.date}-${i}`,
    ticker: c.ticker,
    text: `sentiment ${c.score >= 0 ? "+" : ""}${c.score.toFixed(2)} · vol ${c.volume.toLocaleString()}`,
    tone: c.score > 0.1 ? "pos" : c.score < -0.1 ? "neg" : "neutral",
  }));
}

export function NewsTickerBar() {
  const q = useQuery({
    queryKey: ["altdata-sentiment-ticker"],
    queryFn: () => api.altSentiment("SPY"),
    refetchInterval: 60_000,
  });
  const items = adapt(q.data ?? []);
  const isEmpty = items.length === 0;
  // Duplicate the list so the keyframe translateX(-50%) produces a seamless loop.
  const looped = isEmpty ? [] : [...items, ...items];

  return (
    <div
      className="ticker-scroll-host relative flex h-7 items-center overflow-hidden border-b border-border bg-bg"
      aria-label="market headlines"
    >
      <span className="z-10 flex h-full shrink-0 items-center gap-1 border-r border-border bg-surface-2 px-3 font-mono text-[10px] uppercase tracking-[0.2em] text-text-dim">
        <span className={isEmpty ? "text-muted" : "text-success"}>●</span>
        alt-data
      </span>
      {isEmpty ? (
        <span className="px-6 font-mono text-[11px] text-muted">
          no live wire — set FINNHUB_API_KEY (or POLYGON_NEWS_KEY) and run the
          sentiment pipeline to populate
        </span>
      ) : (
        <div className="ticker-scroll flex shrink-0 items-center gap-6 whitespace-nowrap pl-6 font-mono text-[11px]">
          {looped.map((it, i) => (
            <span key={`${it.key}-${i}`} className="flex items-center gap-2">
              <span className="text-text">{it.ticker}</span>
              <span className={toneClass(it.tone)}>{arrow(it.tone)}</span>
              <span className="text-text-dim">{it.text}</span>
              <span className="text-border">·</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
