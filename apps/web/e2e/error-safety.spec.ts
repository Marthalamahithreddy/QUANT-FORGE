import { DATA, alerts, expect, expectNoInternalDetail, noPageOverflow, settled, test } from "./support";

test.describe("Failures are stated safely", () => {
  test("a 503 is a temporary-unavailability state, not an empty list and not a crash", async ({ page, fixture }) => {
    await fixture.fault({ match: "/api/v1/filings", status: 503, body: { detail: "Database unavailable." } });
    await page.goto("/filings");
    await settled(page);

    await expect(alerts(page)).toHaveCount(1);
    await expect(alerts(page)).toContainText("Temporarily unavailable");
    await expect(page.getByText("No FSDS-backed filings are stored")).toHaveCount(0);
    await expect(page.getByRole("table")).toHaveCount(0);
    await expectNoInternalDetail(page);
    // The shell is intact: navigation still works.
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Earnings" })).toBeVisible();
    await noPageOverflow(page);
  });

  test("a response that drifts from the contract is a contract failure, never rendered as data", async ({ page, fixture }) => {
    await fixture.fault({ match: "/api/v1/earnings-events", status: 200, body: { items: "not-a-list", total: "many", limit: 50, offset: 0 } });
    await page.goto("/earnings");
    await settled(page);

    await expect(alerts(page)).toContainText("does not understand");
    await expect(page.getByRole("table")).toHaveCount(0);
    await expectNoInternalDetail(page);
  });

  test("a stored record that fails its own integrity check is a server failure with a fixed explanation", async ({ page }) => {
    const corrupt = DATA.record("corrupt_manifest");
    await page.goto(`/research/saved/${corrupt.id}`);
    await settled(page);

    await expect(alerts(page)).toHaveCount(1);
    await expect(alerts(page)).toContainText("integrity");
    // It is not "not found", not a verification status, and shows no stored text.
    await expect(page.getByText(/no saved research record has this identifier/i)).toHaveCount(0);
    await expect(page.locator("[data-verification]")).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText(/canonical|manifest|sha256|digest/i);
    await expectNoInternalDetail(page);
  });

  test("an unknown stored record is a not-found page with one h1 and a way back", async ({ page }) => {
    const response = await page.goto("/research/saved/00000000-0000-4000-8000-000000000000");
    // The route streams, so the status line is already sent; the page itself says not found.
    expect(response?.status()).toBeLessThan(500);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("No saved research record has this identifier");
    await expect(page.getByRole("link", { name: "Back to saved research" })).toBeVisible();
    await expectNoInternalDetail(page);
  });

  test("a path that is not a page is a not-found page inside the shell", async ({ page }) => {
    const response = await page.goto("/no-such-page");
    expect(response?.status()).toBe(404);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This page does not exist");
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  });
});
