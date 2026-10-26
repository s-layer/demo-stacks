import { defineConfig, devices } from "@playwright/test";

const port = 8765;

// The Python app serves the built page and answers with a scripted model, so the test needs
// no model provider: `pnpm build` first.
export default defineConfig({
  testDir: "e2e",
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://127.0.0.1:${port}` },
  projects: [{ name: "chromium", use: devices["Desktop Chrome"] }],
  webServer: {
    command: `uv run python -m tests.chat_e2e_server ${port}`,
    cwd: "..",
    url: `http://127.0.0.1:${port}/`,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
  },
});
