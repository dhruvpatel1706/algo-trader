import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0d10",
        surface: "#13161b",
        border: "#1f242c",
        muted: "#7c8290",
        accent: "#22c55e",
        danger: "#ef4444",
        warn: "#f59e0b",
      },
    },
  },
  plugins: [],
};
export default config;
