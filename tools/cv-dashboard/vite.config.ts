/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // reached from the phone through `tailscale serve` (https://<pc>.<tailnet>.ts.net); still bound to 127.0.0.1
    allowedHosts: [".ts.net"],
    proxy: { "/api": { target: "http://127.0.0.1:5174", changeOrigin: false } },
  },
  preview: { host: "127.0.0.1", port: 5173 },
  build: { outDir: "dist", emptyOutDir: true },
  test: { include: ["test/**/*.test.ts"], environment: "node", testTimeout: 30_000 },
});
