"use client";

import { safeFetch } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

type ProposalEntry = {
  rank: number;
  filename: string;
  slug: string;
  title: string;
  rationale: string;
  status: "implemented" | "proposed";
  impl_path: string | null;
};

type WatchlistEntry = {
  symbol: string;
  confluence: number | null;
  direction: string | null;
  rsi: number | null;
  adx: number | null;
  bb_pct_b: number | null;
  trigger: string | null;
};

type ResearchProposalsResponse = {
  last_run_iso: string | null;
  next_run_iso: string | null;
  regime: string | null;
  threshold: number | null;
  notes: string | null;
  proposals: ProposalEntry[];
  watchlist: WatchlistEntry[];
  top_confluence: WatchlistEntry[];
  data_source: string | null;
  funding_status: string | null;
};

function fmtIso(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().slice(0, 19).replace("T", " ") + "z";
  } catch {
    return iso.slice(0, 19);
  }
}

function fmtNum(n: number | null, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

const STATUS_CLS: Record<string, string> = {
  implemented: "border-success/30 bg-success/10 text-success",
  proposed: "border-info/30 bg-info/10 text-info",
};

const DIRECTION_CLS: Record<string, string> = {
  long: "text-success",
  short: "text-danger",
  neutral: "text-text-dim",
};

function confluenceTone(score: number | null, threshold: number | null): string {
  if (score === null || threshold === null) return "text-text-dim";
  if (score >= threshold) return "text-success font-semibold";
  if (score >= threshold * 0.8) return "text-warn";
  return "text-text-dim";
}

export function ResearchProposalsPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ["orchestrator-research-proposals"],
    queryFn: () =>
      safeFetch<ResearchProposalsResponse | null>(
        "/api/orchestrator/research_proposals",
        null,
      ),
    refetchInterval: 60_000,
  });

  const proposals = data?.proposals ?? [];
  const watchlist = data?.watchlist ?? [];
  const topConfluence = data?.top_confluence ?? [];
  const threshold = data?.threshold ?? null;
  const implCount = proposals.filter((p) => p.status === "implemented").length;

  return (
    <section className="rounded-2xl border border-border bg-surface shadow-card-soft">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold tracking-wide text-text">
            researcher proposals
          </h2>
          {data?.regime && (
            <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-text-dim">
              {data.regime}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 font-mono text-[10px] text-text-dim">
          <span>
            {implCount}/{proposals.length} implemented
          </span>
          <span className="text-border">·</span>
          <span>last run {fmtIso(data?.last_run_iso ?? null).slice(11, 19)}z</span>
        </div>
      </header>

      {isLoading ? (
        <div className="px-5 py-6 text-center font-mono text-[11px] text-muted">
          loading…
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 p-3 lg:grid-cols-2">
          {/* Proposals column */}
          <div className="space-y-2">
            <h3 className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
              priority queue
            </h3>
            {proposals.length === 0 ? (
              <p className="font-mono text-[11px] text-muted">
                no proposals yet — researcher hasn't logged a brief
              </p>
            ) : (
              <ul className="space-y-1.5">
                {proposals.map((p) => (
                  <li
                    key={p.slug}
                    className="rounded-lg border border-border bg-bg/40 p-2"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-[10px] text-text-dim">
                            #{p.rank}
                          </span>
                          <span className="truncate font-mono text-[12px] text-text">
                            {p.slug}
                          </span>
                        </div>
                        <p className="mt-0.5 line-clamp-2 font-mono text-[10px] text-text-dim">
                          {p.rationale}
                        </p>
                      </div>
                      <span
                        className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                          STATUS_CLS[p.status] ?? STATUS_CLS.proposed
                        }`}
                      >
                        {p.status === "implemented" ? "shipped" : "queue"}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Watchlist + top confluence column */}
          <div className="space-y-3">
            <div className="space-y-2">
              <h3 className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
                watchlist (next run)
              </h3>
              {watchlist.length === 0 ? (
                <p className="font-mono text-[11px] text-muted">empty</p>
              ) : (
                <ul className="space-y-1.5">
                  {watchlist.map((w) => (
                    <li
                      key={w.symbol}
                      className="rounded-lg border border-border bg-bg/40 p-2"
                    >
                      <div className="flex items-baseline justify-between gap-2 font-mono text-[11px]">
                        <span className="text-text">{w.symbol}</span>
                        <span className="text-text-dim">
                          rsi {fmtNum(w.rsi, 1)} · adx {fmtNum(w.adx, 1)} · bb%
                          {" "}
                          {fmtNum(w.bb_pct_b, 2)}
                        </span>
                      </div>
                      {w.trigger && (
                        <p className="mt-1 font-mono text-[10px] text-text-dim">
                          ↳ {w.trigger}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="space-y-2">
              <h3 className="font-mono text-[10px] uppercase tracking-wider text-text-dim">
                top confluence{threshold !== null ? ` (gate ${fmtNum(threshold, 2)})` : ""}
              </h3>
              {topConfluence.length === 0 ? (
                <p className="font-mono text-[11px] text-muted">empty</p>
              ) : (
                <ul className="grid grid-cols-2 gap-1.5">
                  {topConfluence.map((c) => (
                    <li
                      key={c.symbol}
                      className="flex items-center justify-between rounded border border-border bg-bg/40 px-2 py-1 font-mono text-[10px]"
                    >
                      <span className="text-text">{c.symbol}</span>
                      <span className="flex items-center gap-1.5">
                        <span
                          className={`${
                            DIRECTION_CLS[c.direction ?? "neutral"] ??
                            DIRECTION_CLS.neutral
                          }`}
                        >
                          {c.direction ?? "—"}
                        </span>
                        <span className={confluenceTone(c.confluence, threshold)}>
                          {fmtNum(c.confluence, 2)}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}

      {(data?.data_source || data?.funding_status) && (
        <footer className="border-t border-border bg-bg/40 px-4 py-1.5 font-mono text-[9px] text-text-dim">
          {data.data_source && <span>data: {data.data_source}</span>}
          {data.data_source && data.funding_status && <span> · </span>}
          {data.funding_status && <span>funding: {data.funding_status}</span>}
        </footer>
      )}
    </section>
  );
}
