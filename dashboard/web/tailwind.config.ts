import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        "surface-2": "var(--color-surface-2)",
        border: "var(--color-border)",
        "border-strong": "var(--color-border-strong)",
        muted: "var(--color-muted)",
        text: "var(--color-text)",
        "text-dim": "var(--color-text-dim)",
        primary: "var(--color-primary)",
        secondary: "var(--color-secondary)",
        accent: "var(--color-accent)",
        success: "var(--color-success)",
        danger: "var(--color-danger)",
        warn: "var(--color-warn)",
        info: "var(--color-info)",
      },
      fontFamily: {
        sans: ['"Fira Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"Fira Code"', "ui-monospace", '"SF Mono"', "Menlo", "monospace"],
      },
      boxShadow: {
        "card-soft": "0 1px 0 0 rgba(255,255,255,0.03), 0 8px 24px -12px rgba(0,0,0,0.5)",
        "ring-success": "0 0 0 1px rgba(16, 185, 129, 0.4), 0 0 24px -4px rgba(16, 185, 129, 0.3)",
        "ring-danger": "0 0 0 1px rgba(239, 68, 68, 0.4), 0 0 24px -4px rgba(239, 68, 68, 0.3)",
      },
    },
  },
  plugins: [],
};
export default config;
