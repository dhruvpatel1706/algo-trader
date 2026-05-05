"use client";

import { CostCounter } from "@/components/CostCounter";
import { LivePositionsTable } from "@/components/LivePositionsTable";
import { StrategiesPanel } from "@/components/StrategiesPanel";
import dynamic from "next/dynamic";

// Time-and-data-derived components: render client-only to avoid SSR/CSR
// mismatches from Date.now()/relative timestamps/animation-tied state.
const PnlHero = dynamic(() => import("@/components/PnlHero").then((m) => m.PnlHero), {
  ssr: false,
  loading: () => <div className="h-40 rounded-2xl border border-border bg-surface" />,
});

const EquityChart = dynamic(
  () => import("@/components/EquityChart").then((m) => m.EquityChart),
  {
    ssr: false,
    loading: () => <div className="h-[388px] rounded-2xl border border-border bg-surface" />,
  },
);

const DrawdownGauge = dynamic(
  () => import("@/components/DrawdownGauge").then((m) => m.DrawdownGauge),
  {
    ssr: false,
    loading: () => <div className="h-32 rounded-2xl border border-border bg-surface" />,
  },
);

const PnlPredictor = dynamic(
  () => import("@/components/PnlPredictor").then((m) => m.PnlPredictor),
  {
    ssr: false,
    loading: () => <div className="h-72 rounded-2xl border border-border bg-surface" />,
  },
);

const MonteCarloForecast = dynamic(
  () => import("@/components/MonteCarloForecast").then((m) => m.MonteCarloForecast),
  {
    ssr: false,
    loading: () => <div className="h-[480px] rounded-2xl border border-border bg-surface" />,
  },
);

const AgentActivity = dynamic(
  () => import("@/components/AgentActivity").then((m) => m.AgentActivity),
  {
    ssr: false,
    loading: () => <div className="h-64 rounded-2xl border border-border bg-surface" />,
  },
);

const LiveTradesFeed = dynamic(
  () => import("@/components/LiveTradesFeed").then((m) => m.LiveTradesFeed),
  {
    ssr: false,
    loading: () => <div className="h-96 rounded-2xl border border-border bg-surface" />,
  },
);

const AnalyticsPanel = dynamic(
  () => import("@/components/AnalyticsPanel").then((m) => m.AnalyticsPanel),
  {
    ssr: false,
    loading: () => <div className="h-80 rounded-2xl border border-border bg-surface" />,
  },
);

export function PortfolioView() {
  return (
    <div className="mx-auto max-w-[1600px] space-y-5 p-6">
      <PnlHero />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <EquityChart
            title="joined equity (by agent · 90d)"
            stacked
            drawdown
            height={320}
          />
        </div>
        <div className="space-y-5">
          <DrawdownGauge />
          <StrategiesPanel />
        </div>
      </div>

      <AgentActivity />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <PnlPredictor />
        <MonteCarloForecast />
      </div>

      <LiveTradesFeed />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <AnalyticsPanel />
        </div>
        <CostCounter />
      </div>

      <LivePositionsTable />
    </div>
  );
}
