import { StrategyTable } from "@/components/StrategyTable";
import { TopBar } from "@/components/TopBar";

export default function StrategiesPage() {
  return (
    <main className="min-h-screen bg-bg">
      <TopBar />
      <div className="mx-auto max-w-[1600px] space-y-4 p-6">
        <div>
          <h1 className="text-lg font-bold text-zinc-100">strategies</h1>
          <p className="text-sm text-muted">
            click a row to drill into Sharpe trend, recent trades, halt controls
          </p>
        </div>
        <StrategyTable />
      </div>
    </main>
  );
}
