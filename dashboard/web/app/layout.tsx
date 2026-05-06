import type { Metadata } from "next";
import "./globals.css";
import { QueryProvider } from "@/components/QueryProvider";

export const metadata: Metadata = {
  title: "algo-trader",
  description: "24/7 multi-agent paper trading dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <head>
        <link
          rel="preconnect"
          href="https://fonts.googleapis.com"
        />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin=""
        />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap"
        />
      </head>
      {/*
        suppressHydrationWarning is here because browser extensions
        (Grammarly, password managers, etc.) inject data-* attributes
        on <body> before React hydrates. Without this, React throws a
        hydration mismatch error that aborts rendering of the rest of
        the tree — which manifests as cards rendering their empty
        state ("connect Alpaca key…") even when the backend is healthy.
      */}
      <body className="font-sans antialiased" suppressHydrationWarning>
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
