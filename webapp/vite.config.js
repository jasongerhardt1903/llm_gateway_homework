import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 开发期前端跑在 5173，后端在 8000。用代理而不是绝对 URL：
// 生产构建后前端由 FastAPI 直接托管在同源根路径，两种模式共用相对路径。
// ``/api`` 是控制台自身接口，``/v1`` 是 agent 接口（Chat 页直连），二者都要转发。
const no_buffer = (proxy) => {
  // SSE 必须关掉缓冲，否则代理会攒够一批才转发，流式就退化成"一次性"。
  proxy.on("proxyRes", (proxyRes) => {
    proxyRes.headers["cache-control"] = "no-cache";
    proxyRes.headers["x-accel-buffering"] = "no";
  });
};

export default defineConfig({
  // Tailwind v4 走官方 Vite 插件（CSS-first），设计 token 定义在 src/styles.css 的 @theme。
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, configure: no_buffer },
      // Chat 页直连 agent 的 /v1/tasks:stream（需求：管理与交互层第 7 条）。
      "/v1": { target: "http://127.0.0.1:8000", changeOrigin: true, configure: no_buffer },
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
