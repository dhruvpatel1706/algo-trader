"use client";

import { api } from "@/lib/api";
import { fmtPctSigned, fmtUsd } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

/**
 * Monte Carlo forward equity simulator.
 *
 * Method:
 *   1. Compute daily log returns from the historical equity curve.
 *   2. Resample with replacement (block bootstrap, block_size=5) to preserve
 *      autocorrelation of returns (Lo & MacKinlay style).
 *   3. Run N=1000 forward paths, each `horizon` days long.
 *   4. Report percentile fan (5/25/50/75/95) plus probability of milestones.
 *
 * This is HONEST about its limits — same disclaimer as the linear predictor:
 * compounding past returns isn't a forecast. Regime change destroys it.
 */

const N_PATHS = 1000;
const BLOCK_SIZE = 5;
const HORIZON_DAYS = 252; // 1 trading year

type Percentiles = {
  p5: number;
  p25: number;
  p50: number;
  p75: number;
  p95: number;
};

type ForecastResult = {
  starting: number;
  horizonDays: number;
  byHorizon: { days: number; pct: Percentiles }[]; // raw equity values at each horizon
  prob_double: number;
  prob_drawdown_15: number;
  prob_drawdown_30: number;
  prob_breakeven_or_better: number;
  median_annualized: number;
  worst5_annualized: number;
};

function makeRng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0xffffffff;
  };
}

function logReturns(values: number[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < values.length; i++) {
    const a = values[i - 1] ?? 0;
    const b = values[i] ?? 0;
    if (a > 0 && b > 0) out.push(Math.log(b / a));
  }
  return out;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.floor(q * sorted.length)));
  return sorted[i] ?? 0;
}

function simulate(
  start: number,
  returns: number[],
  horizon: number,
  nPaths: number,
  rng: () => number,
): ForecastResult {
  if (returns.length < 2) {
    return {
      starting: start,
      horizonDays: horizon,
      byHorizon: [],
      prob_double: 0,
      prob_drawdown_15: 0,
      prob_drawdown_30: 0,
      prob_breakeven_or_better: 0,
      median_annualized: 0,
      worst5_annualized: 0,
    };
  }

  const checkpoints = [21, 63, 126, 252].filter((d) => d <= horizon);
  const checkpointEquities: Record<number, number[]> = Object.fromEntries(
    checkpoints.map((c) => [c, [] as number[]]),
  );
  let countDouble = 0;
  let countDD15 = 0;
  let countDD30 = 0;
  let countBreakeven = 0;
  const finalReturns: number[] = [];

  for (let p = 0; p < nPaths; p++) {
    let equity = start;
    let peak = start;
    let maxDd = 0;
    let blockIdx = Math.floor(rng() * Math.max(1, returns.length - BLOCK_SIZE));
    let inBlock = 0;

    for (let d = 1; d <= horizon; d++) {
      if (inBlock >= BLOCK_SIZE) {
        blockIdx = Math.floor(rng() * Math.max(1, returns.length - BLOCK_SIZE));
        inBlock = 0;
      }
      const r = returns[blockIdx + inBlock] ?? 0;
      inBlock++;
      equity = equity * Math.exp(r);
      peak = Math.max(peak, equity);
      const dd = peak > 0 ? (peak - equity) / peak : 0;
      if (dd > maxDd) maxDd = dd;

      if (d in checkpointEquities) checkpointEquities[d]!.push(equity);
    }

    if (equity >= start * 2) countDouble++;
    if (maxDd >= 0.15) countDD15++;
    if (maxDd >= 0.30) countDD30++;
    if (equity >= start) countBreakeven++;
    finalReturns.push((equity / start) ** (252 / horizon) - 1);
  }

  const byHorizon = checkpoints.map((d) => {
    const arr = (checkpointEquities[d] ?? []).slice().sort((a, b) => a - b);
    return {
      days: d,
      pct: {
        p5: quantile(arr, 0.05),
        p25: quantile(arr, 0.25),
        p50: quantile(arr, 0.5),
        p75: quantile(arr, 0.75),
        p95: quantile(arr, 0.95),
      },
    };
  });

  const sortedAnn = finalReturns.slice().sort((a, b) => a - b);
  return {
    starting: start,
    horizonDays: horizon,
    byHorizon,
    prob_double: countDouble / nPaths,
    prob_drawdown_15: countDD15 / nPaths,
    prob_drawdown_30: countDD30 / nPaths,
    prob_breakeven_or_better: countBreakeven / nPaths,
    median_annualized: quantile(sortedAnn, 0.5),
    worst5_annualized: quantile(sortedAnn, 0.05),
  };
}

function pctColor(p: number, target: number, reverse = false): string {
  const ok = reverse ? p <= target : p >= target;
  return ok ? "text-success" : p >= target * 0.6 ? "text-warn" : "text-danger";
}

export function MonteCarloForecast() {
  const equityQ = useQuery({ queryKey: ["equity-mc"], queryFn: api.equity });
  const series = (equityQ.data ?? []).map((p) => p.total);
  const isEmpty = series.length < 30;  // can't bootstrap from fewer than ~30 returns

  const result = useMemo(() => {
    if (isEmpty) return null;
    const ret = logReturns(series);
    const last = series.at(-1) ?? 0;
    const rng = makeRng(series.length * 31 + Math.round(last * 11));
    return simulate(last, ret, HORIZON_DAYS, N_PATHS, rng);
  }, [series, isEmpty]);

  const last = series.at(-1) ?? 0;

  if (isEmpty || !result) {
    return (
      <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-semibold tracking-wide text-text">
              Monte Carlo forecast
            </h2>
          </div>
          <span className="font-mono text-[11px] text-text-dim">
            block bootstrap · 1Y horizon
          </span>
        </header>
        <div className="px-5 py-8 text-center">
          <p className="font-mono text-[12px] text-text-dim">
            need at least 30 days of equity history to forecast
          </p>
          <p className="mt-1 font-mono text-[10px] text-muted">
            currently have {series.length} day{series.length === 1 ? "" : "s"}
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex items-center justify-between border-b border-border px-5 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold tracking-wide text-text">
            Monte Carlo forecast
          </h2>
        </div>
        <span className="font-mono text-[11px] text-text-dim">
          {N_PATHS.toLocaleString()} paths · block bootstrap (k={BLOCK_SIZE}) · {HORIZON_DAYS}d
        </span>
      </header>

      {/* Headline probabilities */}
      <div className="grid grid-cols-2 gap-px bg-border lg:grid-cols-4">
        <Headline
          label="P(breakeven or better)"
          value={`${(result.prob_breakeven_or_better * 100).toFixed(1)}%`}
          color={pctColor(result.prob_breakeven_or_better, 0.6)}
          detail="not losing money in 1Y"
        />
        <Headline
          label="P(double or better)"
          value={`${(result.prob_double * 100).toFixed(1)}%`}
          color={pctColor(result.prob_double, 0.05)}
          detail="100%+ return in 1Y"
        />
        <Headline
          label="P(drawdown ≥ 15%)"
          value={`${(result.prob_drawdown_15 * 100).toFixed(1)}%`}
          color={pctColor(result.prob_drawdown_15, 0.3, true)}
          detail="touching halt zone"
        />
        <Headline
          label="P(drawdown ≥ 30%)"
          value={`${(result.prob_drawdown_30 * 100).toFixed(1)}%`}
          color={pctColor(result.prob_drawdown_30, 0.05, true)}
          detail="serious blow-up"
        />
      </div>

      {/* Annualized returns summary */}
      <div className="grid grid-cols-1 gap-px bg-border md:grid-cols-2">
        <Headline
          label="median annualized"
          value={fmtPctSigned(result.median_annualized)}
          color={
            result.median_annualized > 0.15
              ? "text-success"
              : result.median_annualized > 0
              ? "text-text"
              : "text-danger"
          }
          detail="50th percentile of 1Y outcomes"
        />
        <Headline
          label="5th percentile annualized"
          value={fmtPctSigned(result.worst5_annualized)}
          color={result.worst5_annualized > -0.1 ? "text-text" : "text-danger"}
          detail="stress floor — only 5% of runs were worse"
        />
      </div>

      {/* Fan chart of equity by horizon */}
      <div className="px-5 py-4">
        <div className="mb-3 grid grid-cols-[60px,1fr] items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-text-dim">
          <span>horizon</span>
          <span className="grid grid-cols-5 gap-2">
            <span className="text-right">5%</span>
            <span className="text-right">25%</span>
            <span className="text-right text-text">median</span>
            <span className="text-right">75%</span>
            <span className="text-right">95%</span>
          </span>
        </div>
        <div className="space-y-2">
          {result.byHorizon.map((h) => {
            // Scale visual bar relative to overall result range
            const allMin = Math.min(...result.byHorizon.map((x) => x.pct.p5));
            const allMax = Math.max(...result.byHorizon.map((x) => x.pct.p95));
            const span = allMax - allMin || 1;
            const scale = (v: number) => ((v - allMin) / span) * 100;
            return (
              <div
                key={h.days}
                className="grid grid-cols-[60px,1fr] items-center gap-2 text-[11px]"
              >
                <div className="font-mono text-text-dim">
                  {h.days === 21 ? "1mo" : h.days === 63 ? "3mo" : h.days === 126 ? "6mo" : "1Y"}
                </div>
                <div>
                  <div className="relative h-6 rounded-md bg-bg">
                    {/* 5-95 range */}
                    <div
                      className="absolute top-2 h-2 rounded-full bg-secondary/20"
                      style={{
                        left: `${scale(h.pct.p5)}%`,
                        right: `${100 - scale(h.pct.p95)}%`,
                      }}
                    />
                    {/* 25-75 IQR */}
                    <div
                      className="absolute top-1.5 h-3 rounded bg-secondary/45"
                      style={{
                        left: `${scale(h.pct.p25)}%`,
                        right: `${100 - scale(h.pct.p75)}%`,
                      }}
                    />
                    {/* median tick */}
                    <div
                      className="absolute top-1 h-4 w-[2px] bg-info"
                      style={{ left: `${scale(h.pct.p50)}%` }}
                    />
                    {/* starting equity reference */}
                    <div
                      className="absolute top-0 h-full w-px bg-text-dim/40"
                      style={{ left: `${scale(last)}%` }}
                    />
                  </div>
                  <div className="mt-1 grid grid-cols-5 gap-2 font-mono text-[10px] text-text-dim">
                    <span className="text-right">{fmtUsd(h.pct.p5, { compact: true })}</span>
                    <span className="text-right">{fmtUsd(h.pct.p25, { compact: true })}</span>
                    <span className="text-right text-text">
                      {fmtUsd(h.pct.p50, { compact: true })}
                    </span>
                    <span className="text-right">{fmtUsd(h.pct.p75, { compact: true })}</span>
                    <span className="text-right">{fmtUsd(h.pct.p95, { compact: true })}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <p className="mt-4 font-mono text-[10px] leading-relaxed text-text-dim">
          method: block bootstrap (block size 5d) of historical log returns, {N_PATHS} forward
          paths, percentile bands per horizon. honest caveat: this assumes the future return
          distribution looks like the past — regime change, liquidity events, and macro shocks
          will diverge. drawdown halt at -15% will trigger before the worst-case paths actually
          play out, capping real-world tail risk.
        </p>
      </div>
    </section>
  );
}

function Headline({
  label,
  value,
  color,
  detail,
}: {
  label: string;
  value: string;
  color: string;
  detail: string;
}) {
  return (
    <div className="bg-surface px-5 py-4">
      <div className="font-mono text-[10px] uppercase tracking-wider text-text-dim">{label}</div>
      <div className={`mt-1 font-mono text-2xl font-semibold ${color}`}>{value}</div>
      <div className="font-mono text-[11px] text-text-dim">{detail}</div>
    </div>
  );
}
