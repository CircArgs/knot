import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/spec":    "http://localhost:8000",
      "/graph":   "http://localhost:8000",
      "/healthz": "http://localhost:8000",
      "/auth":    "http://localhost:8000",
      "/ai":      "http://localhost:8001",
      "/er":      "http://localhost:8002",
    },
  },
});
