import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API runs separately; proxying keeps the browser same-origin so no CORS in dev.
    proxy: { "/api": { target: "http://127.0.0.1:8099", changeOrigin: true } },
  },
});
