import { AgentStream } from "@/components/AgentStream";
import { CostCounter } from "@/components/CostCounter";
import { EquityChart } from "@/components/EquityChart";
import { PositionsTable } from "@/components/PositionsTable";
import { StrategiesPanel } from "@/components/StrategiesPanel";
import { TopBar } from "@/components/TopBar";
import { TradeLog } from "@/components/TradeLog";

export default function Page() {
  return (
    <main className="min-h-screen bg-bg">
      <TopBar />
      <div className="mx-auto max-w-[1600px] space-y-4 p-6">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <EquityChart />
          </div>
          <div className="space-y-4">
            <StrategiesPanel />
            <CostCounter />
          </div>
        </div>
        <PositionsTable />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <TradeLog />
          <AgentStream />
        </div>
      </div>
    </main>
  );
}
