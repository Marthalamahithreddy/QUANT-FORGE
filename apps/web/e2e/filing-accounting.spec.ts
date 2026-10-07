import { alerts, DATA, expect, expectNoInternalDetail, noPageOverflow, settled, test } from "./support";

test.describe("Research with an explicit filing", () => {
  test("no filing is linked until the reader supplies an accession and both selectors", async ({ page, fixture }) => {
    await page.goto(`/research/events/${DATA.event}`);
    await settled(page);
    const form = page.getByRole("form", { name: "Research configuration" });
    await expect(form.getByLabel("Accession")).toHaveValue("");
    await expect(page.getByText("No filing is linked unless you supply an accession")).toBeVisible();
    await expect(page.getByText("none (not linked)")).toBeVisible();

    // Nothing in the request names a filing.
    const requests = await fixture.served("/research/observations/");
    expect(requests.length).toBeGreaterThan(0);
    for (const line of requests) expect(line).not.toMatch(/filing_accession|eps_kind|eps_period_class/);
  });

  test("an explicit filing is evaluated, with unverified period and a labelled comparative", async ({ page, fixture }) => {
    await page.goto(`/research/events/${DATA.event}`);
    const form = page.getByRole("form", { name: "Research configuration" });
    await form.getByLabel("Benchmark").selectOption(DATA.benchmark);
    await form.getByLabel("Horizons (sessions after the anchor)").fill("1,2");
    await form.getByLabel("Accession").fill(DATA.filing);
    await form.getByLabel("Period class").selectOption("QUARTER");
    await form.getByLabel("EPS kind").selectOption("DILUTED");
    await form.getByRole("button", { name: "Apply" }).click();

    await expect.poll(() => [...new URL(page.url()).searchParams.keys()].sort()).toEqual([
      "benchmark_id",
      "eps_kind",
      "eps_period_class",
      "filing_accession",
      "horizons",
    ]);
    await settled(page);

    const section = page.getByRole("region", { name: "Accounting and filing", exact: true });
    await expect(section).toContainText("Accounting: EVALUATED");
    await expect(section).toContainText(DATA.filing);

    // The filing is the reader's, and its period is NOT claimed to match the event.
    await expect(section).toContainText("PERIOD_UNVERIFIED");
    await expect(section).toContainText("The filing's fiscal period was not checked against this event");
    await expect(section.getByText(/period (is )?verified/i)).toHaveCount(0);

    // The comparative is the filing's own prior-period column, and says so.
    await expect(section.getByText("As presented in this filing").first()).toBeVisible();
    await expect(section).toContainText("comparative EPS as presented in this filing");
    await expect(section).toContainText("+0.0100");

    // Accounting is descriptive: it carries no point-in-time claim.
    await expect(section).toContainText("no point-in-time claim");
    await expect(alerts(page)).toHaveCount(0);

    const requests = await fixture.served("/research/observations/");
    expect(requests.some((line) => line.includes(`filing_accession=${DATA.filing}`) && line.includes("eps_kind=DILUTED") && line.includes("eps_period_class=QUARTER"))).toBe(true);
    await expectNoInternalDetail(page);
    await noPageOverflow(page);
  });

  test("a refused selector is a stated refusal inside the study, not an error page", async ({ page }) => {
    // Annual selector for a quarterly filing: the captured backend refuses it inside a 200.
    await page.goto(
      `/research/events/${DATA.event}?horizons=1,2&filing_accession=${DATA.filing}&eps_period_class=ANNUAL&eps_kind=DILUTED`,
    );
    await settled(page);
    const section = page.getByRole("region", { name: "Accounting and filing", exact: true });
    await expect(section).toBeVisible();
    await expect(alerts(page)).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Refusal ledger" })).toBeVisible();
  });
});
