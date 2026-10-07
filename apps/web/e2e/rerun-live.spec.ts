import { DATA, alerts, expect, mode, settled, test } from "./support";

test.describe("Re-run this request live", () => {
  test("a stored request opens the live workspace with the supported request parameters only", async ({ page, fixture }) => {
    const record = DATA.record("complete_filing_accounting");
    await page.goto(`/research/saved/${record.id}`);
    await settled(page);

    const link = page.getByRole("link", { name: "Re-run this request live" });
    await expect(link).toBeVisible();
    // A link, not an action: following it is navigation and nothing else.
    expect(await fixture.served("/research/observations/")).toHaveLength(0);
    await link.click();

    await expect(page).toHaveURL(new RegExp(`^.*/research/events/${record.eventId}\\?`));
    const url = new URL(page.url());
    expect([...url.searchParams.keys()].sort()).toEqual(["benchmark_id", "eps_kind", "eps_period_class", "filing_accession", "horizons"]);
    expect(url.searchParams.get("horizons")).toBe("1,2");
    expect(url.searchParams.get("eps_kind")).toBe("DILUTED");
    expect(url.searchParams.get("filing_accession")).toBe("0000320193-24-000010");
    // Nothing that identifies the stored baseline travels.
    expect(url.search).not.toContain(record.id);
    expect(url.search).not.toMatch(/record|fingerprint|evidence|verify|manifest|sha256/i);

    await settled(page);
    // It is a NEW live study, in the live workspace.
    await expect(mode(page, "DESCRIPTIVE")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).not.toHaveText("Saved research record");
    await expect(page.locator("[data-reproducibility]")).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText(/baseline/i);
    await expect(alerts(page)).toHaveCount(0);

    // The live request is the stored request's own parameters, and no record was read again.
    const observation = (await fixture.served("/research/observations/")).join("\n");
    expect(observation).toContain(`benchmark_id=${url.searchParams.get("benchmark_id")}`);
    expect(observation).toContain("eps_period_class=QUARTER");
    expect(await fixture.served(`/research/records/${record.id}`)).toHaveLength(1);
    expect(await fixture.served("/verify")).toHaveLength(0);
  });
});
