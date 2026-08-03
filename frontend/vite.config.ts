import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 5180 / 8010 rather than Vite's 5173 and the crowded 8000: other projects on
// this machine hold those, and a silent fallback to 5174 while /api still
// proxied to someone else's :8000 backend is a confusing failure. strictPort
// makes a collision an immediate error instead.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    proxy: { "/api": "http://localhost:8010" },
  },
  build: { outDir: "dist" },
});
