import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const searchTarget = env.SEARCH_AI_TARGET || "http://127.0.0.1:8002";
  const searchProxy = {
    target: searchTarget,
    changeOrigin: true,
    rewrite: (requestPath: string) => requestPath.replace(/^\/(?:search-api|search-ai)/, ""),
  };

  return {
    plugins: [react()],
    base: env.VITE_BASE_PATH || "./",
    server: {
      host: "127.0.0.1",
      port: 5174,
      strictPort: true,
      proxy: {
        // Both prefixes route to standalone Search AI in local development.
        // The browser calls /search-api/api/search; the backend receives /api/search.
        "/search-api": searchProxy,
        // Also support the planned hosted path after the reverse proxy is configured.
        "/search-ai": searchProxy,
      },
    },
    preview: {
      host: "127.0.0.1",
      port: 4174,
      strictPort: true,
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
