import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { readBuildInfo } from "./build-info.ts";

const apiTarget = process.env.WS_LOCAL_API_TARGET ?? "http://127.0.0.1:8000";
const build = readBuildInfo();

export default defineConfig({
  define: { __WEALTHSAIL_BUILD__: JSON.stringify(build) },
  plugins: [
    react(),
    tailwindcss(),
    {
      name: "wealthsail-build-info",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "build-info.json",
          source: JSON.stringify(build) + "\n",
        });
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      // 开发环境把 /api 代理到本地 FastAPI 后端（uvicorn backend.main:app --port 8000）
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
      // 头像等静态资源
      "/static": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  // `vite preview` is used by CI smoke tests so the browser exercises the
  // production bundle while API requests still reach the local FastAPI process.
  preview: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/static": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    globals: true,
  },
});
