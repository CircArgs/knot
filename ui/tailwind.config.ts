import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        knot: {
          ink:    "#0f172a",
          accent: "#3b82f6",
          muted:  "#64748b",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
