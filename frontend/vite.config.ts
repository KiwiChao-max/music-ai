import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Forward `/api/*` to the FastAPI backend during development.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      // Forward `/storage/*` (worker outputs + uploads) so the dev server
      // can serve them too. In prod, the backend's StaticFiles mount is
      // exposed directly at /storage/*.
      "/storage": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
