import { BacktestRunner } from "@/components/BacktestRunner";
import { TopBar } from "@/components/TopBar";

export default function BacktestsPage() {
  return (
    <main className="min-h-screen bg-bg">
      <TopBar />
      <div className="mx-auto max-w-[1400px] space-y-4 p-6">
        <div>
          <h1 className="text-lg font-bold text-zinc-100">backtests</h1>
          <p className="text-sm text-muted">
            pick a strategy + period, run, and compare to past runs
          </p>
        </div>
        <BacktestRunner />
      </div>
    </main>
  );
}
