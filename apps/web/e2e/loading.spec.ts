import { DATA, alerts, expect, noPageOverflow, test } from "./support";

/**
 * While data is slow the shell is already there (once), the page shows the skeleton of ITS OWN
 * layout, and when data arrives nothing is left busy. `commit` returns as soon as the response
 * starts, before the stream completes.
 */
const SLOW = 1800;

async function shellIsStable(page: import("@playwright/test").Page) {
  await expect(page.getByRole("banner")).toHaveCount(1);
  await expect(page.getByRole("navigation", { name: "Primary" })).toHaveCount(1);
  await expect(page.locator("main")).toHaveCount(1);
}

test.describe("Loading", () => {
  const CASES: [string, string, string, string][] = [
    ["Earnings list", "/api/v1/earnings-events", "/earnings", "Loading earnings events"],
    ["Filings list", "/api/v1/filings", "/filings", "Loading filings"],
    ["Research finder", "/api/v1/earnings-events", "/research", "Loading stored events"],
    ["Saved research", "/api/v1/research/records", "/research/saved", "Loading saved research records"],
  ];
  for (const [name, match, path, label] of CASES) {
    test(`${name}: header and filters at once, a skeleton of the table, then the data`, async ({ page, fixture }) => {
      await fixture.fault({ match, delayMs: SLOW });
      await page.goto(path, { waitUntil: "commit" });
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByRole("status").filter({ hasText: label })).toHaveAttribute("aria-busy", "true");
      await shellIsStable(page);
      await expect(page.getByRole("table")).toHaveCount(0);

      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 10_000 });
      await expect(page.getByRole("table").first()).toBeVisible();
      await shellIsStable(page);
      await expect(alerts(page)).toHaveCount(0);
    });
  }

  test("an event record: the page skeleton, then the record", async ({ page, fixture }) => {
    await fixture.fault({ match: `/earnings-events/${DATA.event}`, delayMs: SLOW });
    await page.goto(`/earnings/${DATA.event}`, { waitUntil: "commit" });
    await expect(page.locator('[aria-busy="true"]').first()).toBeVisible();
    await shellIsStable(page);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
  });

  test("the research result streams behind an already usable configuration", async ({ page, fixture }) => {
    await fixture.fault({ match: "/research/observations/", delayMs: SLOW });
    await page.goto(`/research/events/${DATA.event}`, { waitUntil: "commit" });
    const form = page.getByRole("form", { name: "Research configuration" });
    await expect(form).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "Loading the research result" })).toHaveAttribute("aria-busy", "true");
    await shellIsStable(page);
    await expect(page.getByRole("region", { name: "DESCRIPTIVE", exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    await noPageOverflow(page);
  });

  test("Overview: each region loads on its own", async ({ page, fixture }) => {
    await fixture.fault({ match: "/api/v1/filings", delayMs: SLOW });
    await page.goto("/", { waitUntil: "commit" });
    await expect(page.getByRole("table", { name: "Latest earnings events" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "Loading the latest filings" })).toHaveAttribute("aria-busy", "true");
    await expect(page.getByRole("table", { name: "Latest stored filings" })).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
  });
});
