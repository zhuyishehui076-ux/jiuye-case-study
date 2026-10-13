import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 本地开发时 /api 代理到后端；生产由 nginx 反代。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
