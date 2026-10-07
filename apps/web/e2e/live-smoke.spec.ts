import { expect, test } from "@playwright/test";

/**
 * LIVE SEEDED-BACKEND SMOKE. Not the fixture-backed suite: this runs the production build against a
 * REAL QuantForge API (http://localhost:8000) and whatever its local database holds. It proves the
 * frontend and the real backend agree on the contract for the states that database has, and it
 * claims nothing else: the seeded database has no filings, no saved records and no price bars, so
 * those states are covered only by the fixture-backed suite. Assertions are derived from the live
 * API's own answers, never from remembered seed values.
 */
const API = "http://localhost:8000/api/v1";

test.describe("live seeded backend", () => {
  test("the API is up and the shell reports it", async ({ page, request }) => {
    const health = await request.get(`${API}/health`);
    expect(health.ok(), "start the QuantForge API on :8000 first (see the README)").toBe(true);

    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Overview");
    await expect(page.getByText("Connected")).toBeVisible();
    // Every region answers for itself: a table or its own empty state, never an error.
    await expect(page.locator('main [role="alert"]')).toHaveCount(0);
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
  });

  test("Earnings lists what the API lists", async ({ page, request }) => {
    const body = await (await request.get(`${API}/earnings-events?limit=50&offset=0`)).json();
    await page.goto("/earnings");
    if (body.total === 0) {
      await expect(page.getByText("No earnings events are stored")).toBeVisible();
      return;
    }
    const table = page.getByRole("table", { name: "Earnings events" });
    await expect(table.locator("tbody tr")).toHaveCount(Math.min(body.items.length, 50));
    await expect(page.getByTestId("pagination-position")).toContainText(`of ${body.total} stored earnings events`);
  });

  test("one stored event: its record and the default Research study render without an application error", async ({ page, request }) => {
    const list = await (await request.get(`${API}/earnings-events?limit=1&offset=0`)).json();
    test.skip(list.total === 0, "the live database holds no earnings event");
    const event = list.items[0];

    await page.goto(`/earnings/${event.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(event.company.display_name);
    await expect(page.locator('main [role="alert"]')).toHaveCount(0);

    await page.goto(`/research/events/${event.id}`);
    await expect(page.getByRole("region", { name: "DESCRIPTIVE", exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "POINT-IN-TIME", exact: true })).toBeVisible();
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    // Whatever the database holds, a domain refusal is a stated state, never an application error.
    await expect(page.locator('main [role="alert"]')).toHaveCount(0);
    // Nothing is chosen for the reader: no benchmark and no filing in the address or the form.
    expect(new URL(page.url()).search).toBe("");
  });

  test("Saved research and Filings answer for what the database holds (possibly nothing)", async ({ page, request }) => {
    const records = await (await request.get(`${API}/research/records?limit=1&offset=0`)).json();
    await page.goto("/research/saved");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Saved research");
    if (records.total === 0) await expect(page.getByText("No saved research records are stored")).toBeVisible();
    else await expect(page.getByRole("table", { name: "Saved research records" })).toBeVisible();

    await page.goto("/filings");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Filings");
    await expect(page.locator('main [role="alert"]')).toHaveCount(0);
  });
});
