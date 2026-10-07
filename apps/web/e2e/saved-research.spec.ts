import { DATA, alerts, expect, mode, noPageOverflow, settled, test } from "./support";

test.describe("Saved research", () => {
  test("the list opens a stored baseline, which is shown as stored and never auto-verified", async ({ page, fixture }) => {
    const complete = DATA.record("complete_benchmark");
    await page.goto("/research/saved");
    const table = page.getByRole("table", { name: "Saved research records" });
    await expect(table).toBeVisible();
    // Both levels exist in the captured records, each by its own badge.
    await expect(table.locator('[data-reproducibility="COMPLETE"]').first()).toBeVisible();
    await expect(table.locator('[data-reproducibility="PARTIAL"]').first()).toBeVisible();
    // The list requested no detail and no verification.
    expect(await fixture.served("/verify")).toHaveLength(0);
    expect(await fixture.served(`/research/records/${complete.id}`)).toHaveLength(0);

    await table.locator(`a[href="/research/saved/${complete.id}"]`).click();
    await expect(page).toHaveURL(`/research/saved/${complete.id}`);
    await settled(page);

    // Identity: the whole record id, labelled as the stored baseline, with the level.
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Saved research record");
    await expect(page.getByRole("note", { name: "Stored baseline" })).toBeVisible();
    await expect(page.getByText(complete.id).first()).toBeVisible();
    await expect(page.locator('[data-reproducibility="COMPLETE"]').first()).toBeVisible();

    // The stored result is on the page, in the same frames the live workspace uses.
    await expect(mode(page, "DESCRIPTIVE")).toBeVisible();
    await expect(mode(page, "POINT-IN-TIME")).toBeVisible();

    // Verification is offered, not run.
    await expect(page.getByRole("link", { name: "Verify against current evidence" })).toBeVisible();
    await expect(page.locator("[data-verification]")).toHaveCount(0);
    expect(await fixture.served("/verify")).toHaveLength(0);
    // One detail read for the page, and neither an observation nor an event lookup for the baseline.
    expect(await fixture.served(`/research/records/${complete.id}`)).toHaveLength(1);
    expect(await fixture.served("/research/observations/")).toHaveLength(0);
    await expect(alerts(page)).toHaveCount(0);
    await noPageOverflow(page);
  });

  test("a PARTIAL baseline is caution about the evidence trail, not an error", async ({ page }) => {
    const partial = DATA.record("partial_benchmark");
    await page.goto(`/research/saved/${partial.id}`);
    await settled(page);

    await expect(page.locator('[data-reproducibility="PARTIAL"]').first()).toBeVisible();
    await expect(page.getByText(/Residual assumptions|evidence trail|ingestion run/i).first()).toBeVisible();
    await expect(alerts(page)).toHaveCount(0);
    // The stored result is still whole.
    await expect(mode(page, "DESCRIPTIVE")).toBeVisible();
  });

  test("a stored baseline offers no way to save, edit, delete or rename it", async ({ page }) => {
    await page.goto(`/research/saved/${DATA.record("complete_plain").id}`);
    await settled(page);
    await expect(page.getByRole("button", { name: /save|delete|rename|edit|update|refresh/i })).toHaveCount(0);
    await expect(page.getByText(/Immutable/i).first()).toBeVisible();
  });
});
