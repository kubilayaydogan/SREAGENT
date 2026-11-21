/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.SRE_API_URL ?? "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: { manualChunks: { charts: ["recharts"], markdown: ["react-markdown", "remark-gfm"] } },
    },
  },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
