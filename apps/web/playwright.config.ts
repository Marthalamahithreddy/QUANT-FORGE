import { defineConfig, devices } from "@playwright/test";

/**
 * Fixture-backed browser end-to-end tests. The application under test is the PRODUCTION build
 * (`next build` + `next start`), pointed at e2e/fixture-api.mjs, which serves the real backend
 * responses captured under tests/contract/. Build first with `npm run e2e:build` (or run
 * `npm run e2e`). The API address is fixed into a build, so this one lives in its own directory
 * (`.next-e2e`) and can never be confused with a build made for a real backend (`.next`).
 *
 * Tests run serially in one worker because the fixture API holds one request log and one set of
 * injected faults. System Chrome is used (`channel: "chrome"`), so no browser is downloaded.
 */
export const FIXTURE_API = "http://127.0.0.1:8765";
export const APP = "http://127.0.0.1:3100";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: /live-smoke/,
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  reporter: [["list"]],
  use: {
    baseURL: APP,
    channel: "chrome",
    viewport: { width: 1440, height: 900 },
    colorScheme: "dark",
    trace: "retain-on-failure",
  },
  projects: [{ name: "fixture-backed", use: { ...devices["Desktop Chrome"], channel: "chrome", viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: "node e2e/fixture-api.mjs",
      url: `${FIXTURE_API}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "QF_DIST_DIR=.next-e2e npx next start -p 3100",
      url: APP,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
