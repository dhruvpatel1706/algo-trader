import { PortfolioView } from "@/components/PortfolioView";
import { TopBar } from "@/components/TopBar";

export default function Page() {
  return (
    <main className="min-h-screen bg-bg">
      <TopBar />
      <PortfolioView />
    </main>
  );
}
