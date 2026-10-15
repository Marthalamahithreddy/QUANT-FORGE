import { DATA, alerts, expect, expectNoInternalDetail, noPageOverflow, settled, test } from "./support";

test.describe("Filings", () => {
  test("the index opens one filing's descriptive EPS, with no time or tradability claim", async ({ page, fixture }) => {
    await page.goto("/filings");
    const table = page.getByRole("table", { name: "Stored filings with SEC FSDS data" });
    await expect(table).toBeVisible();
    await expect(page.getByText(/not every SEC filing/i)).toBeVisible();

    await table.getByRole("link", { name: new RegExp(`^Filing ${DATA.filing}, 10-Q, CIK 0000320193$`) }).click();
    await expect(page).toHaveURL(new RegExp(`/filings/${DATA.filing}\\?`));
    await settled(page);

    await expect(page.getByRole("heading", { level: 1 })).toHaveText(DATA.filing);
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Filings");
    // One EPS read, for the selectors in the address, and it states what it does not claim.
    expect(await fixture.served("/eps")).toHaveLength(1);
    await expect(page.locator("main")).toContainText(/descriptive/i);
    await expect(page.locator("main")).not.toContainText(/tradable on|available to trade|first public/i);
    await expect(alerts(page)).toHaveCount(0);
    await expectNoInternalDetail(page);
    await noPageOverflow(page);
  });

  test("the navigation lists only real pages and no placeholder", async ({ page }) => {
    await page.goto("/filings");
    const links = await page.getByRole("navigation", { name: "Primary" }).getByRole("link").allInnerTexts();
    expect(links.slice(-6)).toEqual(["Overview", "Companies", "Earnings", "Filings", "Research", "Saved research"]);
    for (const text of links) expect(text).not.toMatch(/backtest|coming soon|planned/i);
  });
});
