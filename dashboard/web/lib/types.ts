export type Portfolio = {
  connected: boolean;
  reason?: string;
  equity?: number;
  cash?: number;
  buying_power?: number;
  portfolio_value?: number;
  last_equity?: number;
  day_change_usd?: number;
  day_change_pct?: number;
  account_blocked?: boolean;
  trading_blocked?: boolean;
  paper?: boolean;
};

export type Position = {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  current_price: number;
  side: string;
};

export type Order = {
  id: string;
  client_order_id: string;
  symbol: string;
  qty: number | null;
  side: string;
  type: string;
  limit_price: number | null;
  status: string;
  submitted_at: string | null;
  filled_qty: number;
  filled_avg_price: number | null;
};

export type Trade = {
  ts?: string;
  cycle_id?: string;
  event: string;
  subject?: string;
  symbol?: string;
  qty?: number;
  side?: string;
  status?: string;
  pnl?: number;
};

export type StrategyStatus = {
  name: string;
  enabled: boolean;
  paused_at: string | null;
};

export type HaltStatus = {
  halted: boolean;
  reason: string | null;
  at: string | null;
};

export type Costs = {
  llm_input_tokens: number;
  llm_output_tokens: number;
  api_requests: number;
  estimated_usd: number;
};

export type AgentEvent = {
  ts?: string;
  agent?: string;
  action?: string;
  duration_ms?: number;
  tokens?: number;
  cost_usd?: number;
  result?: string;
  [k: string]: unknown;
};

export type TrailingMetrics = Record<
  string,
  {
    n_trades: number;
    win_rate: number;
    profit_factor: number;
    expectancy: number;
    total_pnl: number;
  }
>;

// ---- v2: multi-agent / alt-data extensions -------------------------------

// Asset classes the backend currently emits. `fx`/`options`/`futures` are kept
// for future use but are not produced by /api/agents in v1.
export type AssetClass =
  | "equity"
  | "gold"
  | "silver"
  | "bonds"
  | "crypto"
  | "governance"
  | "fx"
  | "options"
  | "futures"
  | "other";

// What `GET /api/agents` actually returns in v1 (shape mirrors AgentSummary
// in dashboard/api/multi_agent.py). Kept deliberately small — richer fields
// (pnl, win rate, strategies list) become available only after the runner
// has been emitting events for a while.
export type Agent = {
  name: string;
  asset_class: AssetClass;
  state: "paper" | "live" | "halted" | "warmup" | "active" | string;
  heat_allocation: number; // 0..1 fraction of total portfolio heat
  coherence: number | null; // live_WR / backtest_WR; null if no live data yet
  n_open_positions: number;
  last_eval_ts: string | null;
};

export type EquityPoint = {
  ts: string;
  total: number;
  by_agent?: Record<string, number>;
  drawdown?: number;
};

export type LivePosition = Position & {
  agent?: string;
  strategy?: string;
};

export type Signal = {
  id: string;
  ts: string;
  agent: string;
  strategy: string;
  symbol: string;
  side: "buy" | "sell" | "flat";
  confidence: number;
  reason?: string;
  source?: string;
};

// What `GET /api/backtest/history` returns — slimmer than the speculative
// frontend-only shape. Optional fields preserved so callers that read richer
// data via `runBacktest()` don't have to type-narrow.
export type BacktestRun = {
  run_id: string;
  ts: string;
  sharpe: number | null;
  max_dd: number | null;
  n_trades: number | null;
  // Speculative fields — populated only by `runBacktest()` POST result.
  id?: string;
  strategy?: string;
  symbol?: string;
  start?: string;
  end?: string;
  total_return?: number;
  max_drawdown?: number;
  win_rate?: number;
  created_at?: string;
};

// Matches backend `/api/coherence?strategy=<name>` shape (CoherenceResponse in
// dashboard/api/multi_agent.py). Per-strategy, not per-agent — the field is
// `strategy`, not `agent`. `coherence` is the live_WR / backtest_WR ratio
// or null when no live data exists yet.
export type CoherenceState = {
  strategy: string;
  coherence: number | null;
  live_win_rate: number | null;
  backtest_win_rate: number | null;
  halted: boolean;
  halt_reason: string | null;
};

export type InsiderTrade = {
  ts: string;
  ticker: string;
  insider: string;
  title?: string;
  side: "buy" | "sell";
  shares: number;
  value_usd: number;
  cluster_id?: string | null;
};

export type SentimentCell = {
  ticker: string;
  date: string;
  score: number; // -1 .. 1
  volume: number;
};

export type WalletTrade = {
  ts: string;
  wallet: string;
  label?: string;
  chain: string;
  token: string;
  side: "buy" | "sell";
  size_usd: number;
};

export type LlmGovernance = {
  policy: string;
  budget_usd: number;
  spent_usd: number;
  tokens_today: number;
  rate_limit_per_min: number;
};

export type MoonshotStatus = {
  active: boolean;
  name?: string;
  notes?: string;
  trigger_count?: number;
};

export type BotStatus = {
  state: "running" | "stopped" | "crashed";
  pid: number | null;
  started_at: string | null;
  uptime_sec: number | null;
  exit_code: number | null;
  log_tail: string[];
  adopted: boolean;
};

