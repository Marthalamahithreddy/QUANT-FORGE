import { defineConfig, devices } from "@playwright/test";

/**
 * Live seeded-backend smoke test: the normal production build against a REAL QuantForge API on
 * http://localhost:8000 and its seeded local database (start both yourself; see the README). It is
 * deliberately small, and it claims nothing about states the seeded database does not hold.
 * Run with `npm run e2e:live`.
 */
export const LIVE_APP = "http://127.0.0.1:3101";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /live-smoke/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  reporter: [["list"]],
  use: { baseURL: LIVE_APP, channel: "chrome", viewport: { width: 1440, height: 900 }, colorScheme: "dark", trace: "retain-on-failure" },
  projects: [{ name: "live-seeded-backend", use: { ...devices["Desktop Chrome"], channel: "chrome", viewport: { width: 1440, height: 900 } } }],
  webServer: { command: "npx next start -p 3101", url: LIVE_APP, reuseExistingServer: false, timeout: 60_000 },
});
