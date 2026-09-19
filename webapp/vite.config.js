import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发期前端跑在 5173，控制台 API 在 8000。用代理而不是绝对 URL：
// 生产构建后前端由 FastAPI 直接托管在同源根路径，两种模式共用相对路径 ``/api``。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // SSE 必须关掉缓冲，否则代理会攒够一批才转发，流式就退化成"一次性"。
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            proxyRes.headers["cache-control"] = "no-cache";
            proxyRes.headers["x-accel-buffering"] = "no";
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    // 冒烟测试用 react-dom/server 做静态渲染，不需要 jsdom。
    environment: "node",
    include: ["src/**/*.test.{js,jsx}"],
  },
});
