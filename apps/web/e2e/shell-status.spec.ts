import { expect, test } from "./support";

/**
 * The status chip is read once per document load (the shell persists across client navigations).
 * These tests record what that means in practice, so the decision to keep it is evidence-backed:
 * it is stated honestly in the chip itself, and every page states its OWN failures, so a stale
 * "Connected" never leaves a failure unexplained.
 */
test.describe("Shell status chip", () => {
  test("states when it was checked, and is refreshed by a document load", async ({ page, fixture }) => {
    await page.goto("/");
    const chip = page.getByTitle("Checked when this page was loaded");
    await expect(chip).toContainText("Connected");

    // The API goes away after the page loaded. A client navigation does not re-check it ...
    await fixture.fault({ match: "/api/v1/health", status: 503 });
    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Earnings" }).click();
    await expect(page).toHaveURL("/earnings");
    await expect(chip).toContainText("Connected");
    // ... and the page itself still reads its own data and states its own result.
    await expect(page.getByRole("table", { name: "Earnings events" })).toBeVisible();

    // A document load re-checks it, and the answer is honest.
    await page.reload();
    await expect(page.getByTitle("Checked when this page was loaded")).toContainText("Unavailable");
  });

  test("a page whose own data fails says so, whatever the chip last said", async ({ page, fixture }) => {
    await page.goto("/");
    await expect(page.getByTitle("Checked when this page was loaded")).toContainText("Connected");
    await fixture.fault({ match: "/api/v1/filings", status: 503 });
    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Filings" }).click();
    await expect(page.locator('main [role="alert"]')).toContainText("Temporarily unavailable");
  });
});
