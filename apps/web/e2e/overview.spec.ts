import { DATA, alerts, expect, expectNoInternalDetail, noPageOverflow, settled, test } from "./support";

test.describe("Overview", () => {
  test("is a launchpad: bounded independent reads, real destinations, no invented figures", async ({ page, fixture }) => {
    await page.goto("/");
    await settled(page);

    expect((await fixture.log()).sort()).toEqual(
      [
        "GET /api/v1/earnings-events?limit=5&offset=0",
        "GET /api/v1/filings?limit=5&offset=0",
        "GET /api/v1/health",
        "GET /api/v1/health/ready",
        "GET /api/v1/research/records?limit=5&offset=0",
      ].sort(),
    );

    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Overview");
    await expect(page.getByRole("table", { name: "Latest earnings events" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Latest stored filings" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Recent saved research records" })).toBeVisible();
    await expect(page.getByText(/^Showing \d+ of \d+ stored earnings events\.$/)).toBeVisible();
    await expect(alerts(page)).toHaveCount(0);
    await expectNoInternalDetail(page);
    await noPageOverflow(page);
  });

  test("one source being down leaves the other regions and the research entry usable", async ({ page, fixture }) => {
    await fixture.fault({ match: "/api/v1/filings", status: 503 });
    await page.goto("/");
    await settled(page);

    await expect(alerts(page)).toHaveCount(1);
    await expect(alerts(page)).toContainText("the latest filings");
    await expect(page.getByRole("table", { name: "Latest earnings events" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Recent saved research records" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open Research" })).toBeVisible();
  });

  test("its links start real workflows", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Open Research" }).click();
    await expect(page).toHaveURL("/research");

    await page.goto("/");
    await page.getByRole("table", { name: "Latest earnings events" }).locator(`a[href="/research/events/${DATA.event}"]`).click();
    await expect(page).toHaveURL(`/research/events/${DATA.event}`);

    await page.goto("/");
    const record = page.getByRole("table", { name: "Recent saved research records" }).getByRole("link").first();
    const href = await record.getAttribute("href");
    await record.click();
    await expect(page).toHaveURL(href!);
    await expect(page.getByRole("note", { name: "Stored baseline" })).toBeVisible();

    await page.goto("/");
    const filing = page.getByRole("table", { name: "Latest stored filings" }).getByRole("link").first();
    const filingHref = await filing.getAttribute("href");
    await filing.click();
    await expect(page).toHaveURL(filingHref!);
  });
});
