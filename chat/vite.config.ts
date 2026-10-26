import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the Python app (`demo-stacks chat`) answers the agent and the chart rows; the
// proxy keeps them on this page's origin, so the session cookie reaches both.
const api = process.env.DEMO_CHAT_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/agent": api, "/flint": api } },
  // Flint loads each chart library it can draw with on demand, Chart.js among them. This app
  // draws with Vega-Lite only, so Chart.js is left out rather than installed.
  build: { rolldownOptions: { external: ["chart.js"] } },
});
