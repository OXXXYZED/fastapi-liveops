import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by FastAPI under /ui (LIVEOPS_ADMIN_UI_DIR). In dev, API calls are proxied.
export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: {
    proxy: {
      "/admin": "http://localhost:8000",
      "/v1": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
    },
  },
});
