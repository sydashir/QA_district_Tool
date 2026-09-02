import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.VITE_PORT ?? 5173),
    // The API runs separately; proxying keeps the browser same-origin so no CORS in dev.
    // VITE_API_TARGET exists so a second dev server can be pointed at a scratch API — the one
    // scripts/seed_demo.py fills — without editing this file and risking the edit being committed.
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8099",
        changeOrigin: true,
      },
    },
  },
});
