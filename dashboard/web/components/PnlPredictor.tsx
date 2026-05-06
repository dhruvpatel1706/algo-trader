"use client";

import { api } from "@/lib/api";
import { fmtUsd, fmtUsdSigned, fmtPctSigned } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

/**
 * Forward P&L extrapolation.
 *
 * Methodology — disclosed to the user (no secret math):
 *   1. Fit linear regression on log(equity) over the visible window
 *      (default last 60 trading days).
 *   2. Compound the slope forward 30/60/90 trading days.
 *   3. Bootstrap residuals 256 times to compute a 90% CI.
 *
 * This is a HEURISTIC — it shows what compounding the current rate looks
 * like, not a forecast. The component shows the "extrapolation" label
 * prominently to keep that honest.
 */

type Projection = {
  horizon_days: number;
  central: number; // projected equity
  lo: number; // 5th percentile
  hi: number; // 95th percentile
  pnl: number; // central - last
  pnl_pct: number;
};

function logRegression(values: number[]): { slope: number; intercept: number; resid: number[] } {
  const xs = values.map((_, i) => i);
  const ys = values.map((v) => Math.log(Math.max(v, 1e-9)));
  const n = xs.length;
  const meanX = xs.reduce((a, b) => a + b, 0) / n;
  const meanY = ys.reduce((a, b) => a + b, 0) / n;
  const num = xs.reduce((acc, x, i) => acc + (x - meanX) * ((ys[i] ?? 0) - meanY), 0);
  const den = xs.reduce((acc, x) => acc + (x - meanX) ** 2, 0) || 1;
  const slope = num / den;
  const intercept = meanY - slope * meanX;
  const resid = ys.map((y, i) => y - (intercept + slope * (xs[i] ?? 0)));
  return { slope, intercept, resid };
}

// Deterministic LCG keeps server and client renders identical (no hydration mismatch).
function makeRng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0xffffffff;
  };
}

function bootstrapPercentile(
  resid: number[],
  k: number,
  slope: number,
  lastLogValue: number,
  rng: () => number,
) {
  if (resid.length === 0) return Math.exp(lastLogValue + slope * k);
  let acc = 0;
  for (let i = 0; i < k; i++) {
    acc += resid[Math.floor(rng() * resid.length)] ?? 0;
  }
  return Math.exp(lastLogValue + slope * k + acc / Math.max(1, k));
}

function project(values: number[], horizons: number[]): Projection[] {
  if (values.length < 5) return [];
  const last = values.at(-1)!;
  const lastLog = Math.log(Math.max(last, 1e-9));
  const { slope, resid } = logRegression(values);
  // Seed depends on input length so the result is stable across SSR/CSR for the same data.
  const rng = makeRng(values.length * 9001 + Math.round(last * 7));
  return horizons.map((k) => {
    const central = Math.exp(lastLog + slope * k);
    const samples: number[] = [];
    for (let i = 0; i < 256; i++) {
      samples.push(bootstrapPercentile(resid, k, slope, lastLog, rng));
    }
    samples.sort((a, b) => a - b);
    const lo = samples[Math.floor(samples.length * 0.05)] ?? central;
    const hi = samples[Math.floor(samples.length * 0.95)] ?? central;
    return {
      horizon_days: k,
      central,
      lo,
      hi,
      pnl: central - last,
      pnl_pct: last > 0 ? (central - last) / last : 0,
    };
  });
}

function annualizedReturn(values: number[]): number {
  if (values.length < 2) return 0;
  const first = values[0] ?? 1;
  const last = values.at(-1) ?? 1;
  const days = values.length;
  return (last / first) ** (252 / days) - 1;
}

export function PnlPredictor() {
  const equityQ = useQuery({ queryKey: ["equity-predict"], queryFn: api.equity });
  const series = useMemo(() => (equityQ.data ?? []).map((p) => p.total), [equityQ.data]);
  const isEmpty = series.length < 30;
  const last = series.at(-1) ?? 0;
  const projections = useMemo(
    () => (isEmpty ? [] : project(series, [30, 60, 90, 252])),
    [series, isEmpty],
  );
  const annRet = isEmpty ? 0 : annualizedReturn(series);

  if (isEmpty) {
    return (
      <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <h2 className="text-sm font-semibold tracking-wide text-text">P&amp;L extrapolation</h2>
          <span className="font-mono text-[11px] text-text-dim">
            log-linear fit · 90% CI
          </span>
        </header>
        <div className="px-5 py-8 text-center font-mono text-[11px] text-muted">
          need at least 30 days of equity history to extrapolate
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex items-center justify-between border-b border-border px-5 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold tracking-wide text-text">P&L extrapolation</h2>
        </div>
        <div className="font-mono text-[11px] text-text-dim">
          fit on log(equity) · 90% CI · {series.length} samples
        </div>
      </header>
      <div className="px-5 py-4">
        <div className="mb-4 flex items-baseline gap-3">
          <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
            implied annualized return
          </span>
          <span
            className={`font-mono text-2xl font-semibold ${
              annRet > 0 ? "text-success" : annRet < 0 ? "text-danger" : "text-text"
            }`}
          >
            {fmtPctSigned(annRet)}
          </span>
          <span className="font-mono text-[11px] text-text-dim">
            from {fmtUsd(series[0] ?? 0, { compact: true })} → {fmtUsd(last, { compact: true })}
          </span>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {projections.map((p) => (
            <div
              key={p.horizon_days}
              className="rounded-xl border border-border bg-bg p-4"
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
                  +{p.horizon_days}d
                </span>
                <span className="font-mono text-[10px] text-text-dim">
                  {p.horizon_days === 252 ? "1Y" : `${Math.round(p.horizon_days / 21)}mo`}
                </span>
              </div>
              <div className="font-mono text-xl font-semibold text-text">
                {fmtUsd(p.central, { compact: true })}
              </div>
              <div
                className={`font-mono text-sm ${
                  p.pnl > 0 ? "text-success" : p.pnl < 0 ? "text-danger" : "text-text-dim"
                }`}
              >
                {fmtUsdSigned(p.pnl)} · {fmtPctSigned(p.pnl_pct)}
              </div>
              <div className="mt-2 font-mono text-[10px] text-text-dim">
                90% CI: {fmtUsd(p.lo, { compact: true })} → {fmtUsd(p.hi, { compact: true })}
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
                <div
                  className="h-full bg-gradient-to-r from-secondary via-info to-success"
                  style={{
                    width: `${Math.min(
                      100,
                      Math.max(0, ((p.central - p.lo) / Math.max(1, p.hi - p.lo)) * 100),
                    )}%`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
        <p className="mt-4 font-mono text-[10px] leading-relaxed text-text-dim">
          extrapolation, not forecast. compounds the in-sample trend forward; CI bootstraps
          residuals from the same window. real-world drawdown will diverge — see drawdown gauge
          and live coherence ratio.
        </p>
      </div>
    </section>
  );
}
