import type {
  Agent,
  AgentEvent,
  BacktestRun,
  BotStatus,
  CoherenceState,
  Costs,
  EquityPoint,
  FeedsStatusResponse,
  HaltStatus,
  InsiderTrade,
  LivePosition,
  LlmGovernance,
  MoonshotStatus,
  Order,
  Portfolio,
  Position,
  SentimentCell,
  Signal,
  StrategyStatus,
  Trade,
  TrailingMetrics,
  WalletTrade,
} from "./types";

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) throw new Error(`${path} -> ${r.status}: ${await r.text()}`);
  return r.json();
}

/**
 * Fetch a JSON endpoint that may not exist yet. On any failure (network,
 * non-2xx, parse error) returns the supplied fallback so callers can still
 * render an empty state rather than crashing the page.
 */
export async function safeFetch<T>(url: string, fallback: T): Promise<T> {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) return fallback;
    return (await r.json()) as T;
  } catch {
    return fallback;
  }
}

export const api = {
  // existing v1 endpoints --------------------------------------------------
  portfolio: () => getJson<Portfolio>("/api/portfolio"),
  positions: () => getJson<Position[]>("/api/positions"),
  orders: (status = "all", limit = 50) =>
    getJson<Order[]>(`/api/orders?status=${status}&limit=${limit}`),
  trades: (from?: string, to?: string) => {
    const q = new URLSearchParams();
    if (from) q.set("from", from);
    if (to) q.set("to", to);
    const qs = q.toString();
    return getJson<Trade[]>(`/api/trades${qs ? `?${qs}` : ""}`);
  },
  strategies: () => getJson<StrategyStatus[]>("/api/strategies"),
  pause: (name: string) => postJson(`/api/strategies/${name}/pause`, {}),
  resume: (name: string) => postJson(`/api/strategies/${name}/resume`, {}),
  halt: () => getJson<HaltStatus>("/api/halt"),
  resetHalt: () => postJson<HaltStatus>("/api/halt/reset", {}),
  metrics: () => getJson<TrailingMetrics>("/api/metrics"),
  costs: () => getJson<Costs>("/api/costs"),
  agentEvents: (limit = 50) => getJson<AgentEvent[]>(`/api/agent-events?limit=${limit}`),
  kill: (reason: string) => postJson("/api/kill", { confirm: "FLATTEN", reason }),

  // Bot lifecycle (Start/Stop from the UI) -------------------------------
  botStatus: () => getJson<BotStatus>("/api/bot/status"),
  botStart: () => postJson<BotStatus>("/api/bot/start", { confirm: "START" }),
  botStop: () => postJson<BotStatus>("/api/bot/stop", { confirm: "STOP" }),

  // v2 endpoints (graceful fallback) --------------------------------------
  // Backend may wrap arrays in objects; we unwrap to keep components simple.
  agents: () => safeFetch<Agent[]>("/api/agents", []),
  equity: async (): Promise<EquityPoint[]> => {
    const raw = await safeFetch<{ points?: EquityPoint[] } | EquityPoint[]>(
      "/api/portfolio/equity",
      [],
    );
    if (Array.isArray(raw)) return raw;
    return Array.isArray(raw?.points) ? raw.points : [];
  },
  livePositions: () => safeFetch<LivePosition[]>("/api/positions/live", []),
  feedsStatus: () =>
    safeFetch<FeedsStatusResponse>("/api/feeds/status", {
      feeds: [],
      n_configured: 0,
      n_total: 0,
    }),
  recentSignals: async (limit = 100): Promise<Signal[]> => {
    const raw = await safeFetch<{ signals?: Signal[] } | Signal[]>(
      `/api/signals/recent?limit=${limit}`,
      [],
    );
    if (Array.isArray(raw)) return raw;
    return Array.isArray(raw?.signals) ? raw.signals : [];
  },
  backtestHistory: async (strategy = "mr_etf"): Promise<BacktestRun[]> => {
    const raw = await safeFetch<{ runs?: BacktestRun[] } | BacktestRun[]>(
      `/api/backtest/history?strategy=${encodeURIComponent(strategy)}`,
      [],
    );
    if (Array.isArray(raw)) return raw;
    return Array.isArray(raw?.runs) ? raw.runs : [];
  },
  coherence: async (strategy = "mr_etf"): Promise<CoherenceState[]> => {
    const raw = await safeFetch<CoherenceState | CoherenceState[] | null>(
      `/api/coherence?strategy=${encodeURIComponent(strategy)}`,
      [],
    );
    if (Array.isArray(raw)) return raw;
    return raw ? [raw] : [];
  },
  altInsider: async (ticker = "SPY"): Promise<InsiderTrade[]> => {
    const raw = await safeFetch<
      { transactions?: InsiderTrade[] } | InsiderTrade[]
    >(`/api/altdata/insider?ticker=${encodeURIComponent(ticker)}`, []);
    if (Array.isArray(raw)) return raw;
    return Array.isArray(raw?.transactions) ? raw.transactions : [];
  },
  altSentiment: async (ticker = "SPY"): Promise<SentimentCell[]> => {
    const raw = await safeFetch<
      { cells?: SentimentCell[] } | SentimentCell[]
    >(`/api/altdata/sentiment?ticker=${encodeURIComponent(ticker)}`, []);
    if (Array.isArray(raw)) return raw;
    return Array.isArray(raw?.cells) ? raw.cells : [];
  },
  altWallets: async (ticker = "BTCUSDT"): Promise<WalletTrade[]> => {
    const raw = await safeFetch<
      { trades?: WalletTrade[] } | WalletTrade[]
    >(`/api/altdata/wallets?ticker=${encodeURIComponent(ticker)}`, []);
    if (Array.isArray(raw)) return raw;
    return Array.isArray(raw?.trades) ? raw.trades : [];
  },
  llmGovernance: () =>
    safeFetch<LlmGovernance | null>("/api/llm/governance", null),
  moonshotStatus: () =>
    safeFetch<MoonshotStatus | null>("/api/moonshot/status", null),
  runBacktest: async (payload: {
    strategy: string;
    start: string;
    end: string;
    symbol?: string;
  }): Promise<BacktestRun | null> => {
    try {
      const r = await fetch("/api/backtest/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) return null;
      return (await r.json()) as BacktestRun;
    } catch {
      return null;
    }
  },

};
