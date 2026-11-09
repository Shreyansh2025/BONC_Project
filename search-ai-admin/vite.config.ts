import { defineConfig, loadEnv, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const searchTarget = env.SEARCH_AI_TARGET || "http://127.0.0.1:8002";
  const adminToken = env.SEARCH_ADMIN_API_TOKEN || "";
  const searchProxy: ProxyOptions = {
    target: searchTarget,
    changeOrigin: true,
    rewrite: (requestPath: string) => requestPath.replace(/^\/(?:search-api|search-ai)/, ""),
    configure: (proxy) => {
      proxy.on("proxyReq", (proxyRequest, request) => {
        // Attach the admin token only to admin API calls. The secret is read by
        // Vite's server config and is never exposed through import.meta.env.
        if (request.url?.includes("/api/admin/") && adminToken) {
          proxyRequest.setHeader("Authorization", `Bearer ${adminToken}`);
        }
      });
    },
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
      proxy: { "/search-api": searchProxy, "/search-ai": searchProxy },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
