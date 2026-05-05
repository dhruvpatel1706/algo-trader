import { SignalStream } from "@/components/SignalStream";
import { TopBar } from "@/components/TopBar";

export default function SignalsPage() {
  return (
    <main className="min-h-screen bg-bg">
      <TopBar />
      <div className="mx-auto max-w-[1600px] space-y-4 p-6">
        <div>
          <h1 className="text-lg font-bold text-zinc-100">signals</h1>
          <p className="text-sm text-muted">
            real-time WebSocket feed; falls back to recent REST history if WS is offline
          </p>
        </div>
        <SignalStream />
      </div>
    </main>
  );
}
