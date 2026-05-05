"use client";

import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import type { SentimentCell } from "@/lib/types";

/**
 * Bloomberg-style horizontal scrolling ticker. Renders sentiment cells if the
 * /api/altdata/sentiment endpoint returns any rows; otherwise falls back to
 * a static demo headline list so the operator (running without API keys) still
 * sees the visual.
 */

type TickerItem = {
  key: string;
  ticker: string;
  text: string;
  tone: "pos" | "neg" | "neutral";
};

const DEMO_HEADLINES: TickerItem[] = [
  { key: "d1", ticker: "SPY", text: "ES futures bid into US open · breadth firms ahead of CPI",       tone: "pos" },
  { key: "d2", ticker: "NVDA", text: "options skew flips bid · gamma supportive into earnings week",   tone: "pos" },
  { key: "d3", ticker: "TLT", text: "30y yield holds 4.78 · curve bear-steepens 4bp",                  tone: "neg" },
  { key: "d4", ticker: "GLD", text: "spot gold +0.6% · dollar softens, real yields sub-2.0",           tone: "pos" },
  { key: "d5", ticker: "BTC", text: "BTC reclaims 68k · funding neutral · spot ETF flows positive",    tone: "pos" },
  { key: "d6", ticker: "QQQ", text: "semis lag · NDX-SPX spread narrows 0.4σ · risk-off rotation",     tone: "neg" },
  { key: "d7", ticker: "IWM", text: "small-caps fade open gap · short interest at 18-mo high",         tone: "neg" },
  { key: "d8", ticker: "AAPL", text: "AAPL bid · supplier guidance lift · 7d momentum top decile",     tone: "pos" },
];

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
  const live = q.data ?? [];
  const usingDemo = live.length === 0;
  const items = usingDemo ? DEMO_HEADLINES : adapt(live);
  // Duplicate the list so the keyframe `translateX(-50%)` produces a seamless loop.
  const looped = [...items, ...items];

  return (
    <div
      className="ticker-scroll-host relative flex h-7 items-center overflow-hidden border-b border-border bg-bg"
      aria-label="market headlines"
    >
      <span className="z-10 flex h-full shrink-0 items-center gap-1 border-r border-border bg-surface-2 px-3 font-mono text-[10px] uppercase tracking-[0.2em] text-text-dim">
        <span className={usingDemo ? "text-warn" : "text-success"}>●</span>
        {usingDemo ? "demo wire" : "alt-data"}
      </span>
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
    </div>
  );
}
