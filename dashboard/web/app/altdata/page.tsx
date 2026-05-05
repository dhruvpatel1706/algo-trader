import { AltdataInsiderPanel } from "@/components/AltdataInsiderPanel";
import { AltdataSentimentHeatmap } from "@/components/AltdataSentimentHeatmap";
import { AltdataWalletsPanel } from "@/components/AltdataWalletsPanel";
import { TopBar } from "@/components/TopBar";

export default function AltdataPage() {
  return (
    <main className="min-h-screen bg-bg">
      <TopBar />
      <div className="mx-auto max-w-[1600px] space-y-4 p-6">
        <div>
          <h1 className="text-lg font-bold text-zinc-100">alt-data</h1>
          <p className="text-sm text-muted">
            insider activity, smart-money wallets, news sentiment
          </p>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <AltdataInsiderPanel />
          <AltdataWalletsPanel />
        </div>
        <AltdataSentimentHeatmap />
      </div>
    </main>
  );
}
