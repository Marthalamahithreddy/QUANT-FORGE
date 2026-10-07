import { alerts, DATA, expect, expectNoInternalDetail, mode, noPageOverflow, settled, test } from "./support";

test.describe("Research keeps what it can show when part of a study is refused", () => {
  test("the point-in-time verdict refuses while the descriptive study stays whole", async ({ page }) => {
    // Default study of an event with no availability evidence: the backend refuses point-in-time.
    await page.goto(`/research/events/${DATA.event}`);
    await settled(page);

    const descriptive = mode(page, "DESCRIPTIVE");
    const pit = mode(page, "POINT-IN-TIME");
    await expect(descriptive).toContainText("Available");
    await expect(pit).toContainText("Refused");

    // The descriptive result is fully there: a returns table with measured values.
    await expect(descriptive.getByRole("table", { name: /Descriptive returns by horizon/ })).toBeVisible();
    await expect(descriptive).toContainText("+10.00%");

    // A refusal is a result of the research, never an application error.
    await expect(alerts(page)).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Refusal ledger" })).toBeVisible();
    await expectNoInternalDetail(page);
    await noPageOverflow(page);
  });

  test("a benchmark with no stored data is refused and the stock study is untouched", async ({ page }) => {
    await page.goto(`/research/events/${DATA.event}?horizons=1,2&benchmark_id=${DATA.emptyBenchmark}`);
    await settled(page);

    const descriptive = mode(page, "DESCRIPTIVE");
    await expect(descriptive.getByRole("table", { name: /Descriptive returns by horizon/ })).toBeVisible();
    // The stock returns are present and exact; the benchmark side says why it has nothing.
    await expect(descriptive).toContainText("+10.00%");
    await expect(descriptive).toContainText("+20.00%");
    await expect(descriptive).toContainText("Missing anchor price");
    await expect(alerts(page)).toHaveCount(0);
  });

  test("an unreadable benchmark list leaves the stock-only study untouched", async ({ page, fixture }) => {
    await fixture.fault({ match: "/api/v1/benchmarks", status: 503 });
    await page.goto(`/research/events/${DATA.event}`);
    await settled(page);

    await expect(page.getByText(/The benchmark list could not be read/)).toBeVisible();
    await expect(mode(page, "DESCRIPTIVE").getByRole("table", { name: /Descriptive returns by horizon/ })).toBeVisible();
    await expectNoInternalDetail(page);
  });
});
