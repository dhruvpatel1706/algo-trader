"use client";
import { api } from "@/lib/api";
import type { FeedStatus } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";

/**
 * Compact chip strip showing every external integration the bot uses and
 * whether its credentials are present. Pulls /api/feeds/status which
 * introspects os.environ — no key material crosses the wire (only a 4-char
 * tail preview when configured).
 *
 * Refetches every 30s so a credential rotation is visible without a hard
 * refresh.
 */
export function FeedsStatusBar() {
  const q = useQuery({
    queryKey: ["feeds-status"],
    queryFn: api.feedsStatus,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });

  const data = q.data;
  if (!data || data.feeds.length === 0) return null;

  // Group by category so the chips read in a stable, semantic order.
  const order: FeedStatus["category"][] = [
    "broker",
    "llm",
    "news",
    "data",
    "altdata",
    "alerts",
  ];
  const grouped = order
    .map((cat) => ({ cat, items: data.feeds.filter((f) => f.category === cat) }))
    .filter((g) => g.items.length > 0);

  return (
    <section className="rounded-2xl border border-border bg-surface px-4 py-3 shadow-card-soft">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold tracking-wide text-text">connected feeds</h2>
        <span className="font-mono text-[10px] text-muted">
          {data.n_configured}/{data.n_total} configured
        </span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {grouped.map((g) => (
          <div key={g.cat} className="flex items-center gap-1.5">
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted">
              {g.cat}
            </span>
            <div className="flex flex-wrap gap-1">
              {g.items.map((f) => (
                <FeedChip key={f.env_var} feed={f} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function FeedChip({ feed }: { feed: FeedStatus }) {
  const baseCls =
    "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] transition";
  const cls = feed.configured
    ? "border-success/40 bg-success/10 text-success"
    : "border-border bg-bg text-muted";
  const dot = feed.configured ? "●" : "○";
  // Tooltip shows what features depend on this key — useful when the operator
  // sees a red chip and wonders "do I actually need this?".
  const title = `${feed.env_var}\n` + (
    feed.configured
      ? `configured (last 4: ${feed.preview ?? "—"})\nused for: ${feed.required_for.join(", ")}`
      : `not set\nrequired for: ${feed.required_for.join(", ")}`
  );
  return (
    <span className={`${baseCls} ${cls}`} title={title}>
      <span aria-hidden>{dot}</span>
      <span>{feed.name}</span>
      {feed.configured && feed.preview && (
        <span className="text-muted">{feed.preview}</span>
      )}
    </span>
  );
}
