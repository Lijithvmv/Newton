import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: Vite serves the app on :5173 and proxies API + SSE to the backend on :8770.
// Build: emits static assets into dist/, which the backend serves in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8770",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
