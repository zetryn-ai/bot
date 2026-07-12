import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: `npm run dev` proxies /api to a locally running API (uvicorn :8140).
// Build: `npm run build` emits dist/, copied into the image's
// zetryn_bot/api/static by the Docker build stage.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8140" },
  },
  build: { outDir: "dist" },
});
