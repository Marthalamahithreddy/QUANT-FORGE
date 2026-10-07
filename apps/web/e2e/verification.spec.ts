import { DATA, alerts, expect, expectNoInternalDetail, mode, noPageOverflow, test } from "./support";

const verify = (page: import("@playwright/test").Page) => page.getByRole("link", { name: "Verify against current evidence" });

test.describe("Verification", () => {
  test("MATCH: the verifier's status is shown as returned, and the baseline stays", async ({ page, fixture }) => {
    const record = DATA.record("complete_benchmark");
    await page.goto(`/research/saved/${record.id}`);
    await verify(page).click();
    await expect(page).toHaveURL(`/research/saved/${record.id}?verify=1#verification`);

    await expect(page.locator('[data-verification="MATCH"]')).toBeVisible();
    await expect(page.getByText(/does not show (that )?the result is correct/i).first()).toBeVisible();
    // The baseline is still on the page, unchanged.
    await expect(mode(page, "DESCRIPTIVE")).toBeVisible();
    // Exactly one verification was requested, by this activation.
    expect(await fixture.served("/verify")).toHaveLength(1);
    // Verification does not write: only GETs were ever served.
    for (const line of await fixture.log()) expect(line).toMatch(/^GET /);
    await expect(alerts(page)).toHaveCount(0);
    await noPageOverflow(page);
  });

  test("the baseline streams on its own while the verification is pending", async ({ page, fixture }) => {
    const record = DATA.record("complete_benchmark");
    await fixture.fault({ match: "/verify", delayMs: 2500 });
    // `commit`: do not wait for the whole stream, which is exactly what is being observed.
    await page.goto(`/research/saved/${record.id}?verify=1`, { waitUntil: "commit" });

    // The verification is still running, and the baseline is already fully readable beside it.
    await expect(page.getByRole("status").filter({ hasText: "Verifying this baseline against the evidence stored now" })).toHaveAttribute("aria-busy", "true");
    await expect(mode(page, "DESCRIPTIVE")).toBeVisible();
    await expect(page.locator("[data-verification]")).toHaveCount(0);

    await expect(page.locator('[data-verification="MATCH"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
  });

  test("MANIFEST_CHANGED lists the backend's changed paths and shows both sides' fingerprints", async ({ page }) => {
    const record = DATA.record("manifest_changed");
    await page.goto(`/research/saved/${record.id}?verify=1`);
    const badge = page.locator('[data-verification="MANIFEST_CHANGED"]');
    await expect(badge).toBeVisible();

    await expect(page.getByText("evidence.stock.bars[1].close")).toBeVisible();
    await expect(page.getByText("Changed manifest sections")).toBeVisible();
    await expect(page.getByText(/this page does not compare financial values/i)).toBeVisible();
    // The baseline remains visible and is not replaced.
    await expect(mode(page, "DESCRIPTIVE")).toBeVisible();
    await expect(alerts(page)).toHaveCount(0);
    await noPageOverflow(page);
  });

  test("RESULT_CHANGED lists only result paths", async ({ page }) => {
    const record = DATA.record("result_changed");
    await page.goto(`/research/saved/${record.id}?verify=1`);
    await expect(page.locator('[data-verification="RESULT_CHANGED"]')).toBeVisible();
    await expect(page.getByText("event.reported_eps")).toBeVisible();
    await expect(page.getByText("Changed manifest paths")).toHaveCount(0);
    await expect(alerts(page)).toHaveCount(0);
  });

  test("EVIDENCE_INCOMPLETE is a genuine match over partial evidence, not a change", async ({ page }) => {
    const record = DATA.record("partial_benchmark");
    await page.goto(`/research/saved/${record.id}?verify=1`);
    await expect(page.locator('[data-verification="EVIDENCE_INCOMPLETE"]')).toBeVisible();
    await expect(alerts(page)).toHaveCount(0);
    await expectNoInternalDetail(page);
  });

  test("a failed verification is an HTTP failure beside the baseline, never a status", async ({ page }) => {
    const record = DATA.record("event_missing");
    await page.goto(`/research/saved/${record.id}?verify=1`);
    await expect(page.getByText("This record cannot be re-run")).toBeVisible();
    await expect(page.locator("[data-verification]")).toHaveCount(0);
    // The stored baseline is untouched.
    await expect(page.getByRole("note", { name: "Stored baseline" })).toBeVisible();
    await expectNoInternalDetail(page);
  });
});
