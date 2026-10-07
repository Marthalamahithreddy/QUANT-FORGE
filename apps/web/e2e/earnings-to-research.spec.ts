import { DATA, expect, mode, noPageOverflow, settled, test } from "./support";

test.describe("Earnings to Research", () => {
  test("a reader goes from the list to a configured study, and every choice is in the address", async ({ page, fixture }) => {
    await page.goto("/earnings");
    const table = page.getByRole("table", { name: "Earnings events" });
    await expect(table).toBeVisible();

    // The reader picks one event from the list; its own link carries its identity.
    await table.locator(`a[href="/earnings/${DATA.event}"]`).click();
    await expect(page).toHaveURL(`/earnings/${DATA.event}`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    await page.getByRole("link", { name: /^Open Research for / }).click();
    await expect(page).toHaveURL(`/research/events/${DATA.event}`);

    const form = page.getByRole("form", { name: "Research configuration" });

    // Nothing is chosen for the reader: no benchmark, no filing, the default horizons.
    await expect(form.getByLabel("Benchmark")).toHaveValue("");
    await expect(form.getByLabel("Accession")).toHaveValue("");
    await expect(page.getByText("none (stock returns only)").first()).toBeVisible();

    // The reader configures a benchmark and horizons, and applies.
    await form.getByLabel("Benchmark").selectOption(DATA.benchmark);
    await form.getByLabel("Horizons (sessions after the anchor)").fill("1,2");
    await form.getByRole("button", { name: "Apply" }).click();

    // The form submits empty optional fields; the server settles on ONE canonical address that
    // holds only what the reader chose: no filing, no selectors, nothing else.
    await expect.poll(() => [...new URL(page.url()).searchParams.keys()].sort()).toEqual(["benchmark_id", "horizons"]);
    const url = new URL(page.url());
    expect(url.pathname).toBe(`/research/events/${DATA.event}`);
    expect(url.searchParams.get("benchmark_id")).toBe(DATA.benchmark);
    expect(url.searchParams.get("horizons")).toBe("1,2");
    await settled(page);

    // The study renders, with the two research modes in separate frames.
    const descriptive = mode(page, "DESCRIPTIVE");
    const pit = mode(page, "POINT-IN-TIME");
    await expect(descriptive).toBeVisible();
    await expect(pit).toBeVisible();
    await expect(descriptive.getByRole("table").first()).toBeVisible();

    // The benchmark shown, and the one selected after the reload, are the one the reader chose.
    await expect(form.getByLabel("Benchmark")).toHaveValue(DATA.benchmark);
    await expect(page.getByText("Observation Index").first()).toBeVisible();
    await noPageOverflow(page);

    // One observation request per applied configuration, and it carried exactly the choices.
    const observations = await fixture.served("/research/observations/");
    expect(observations.some((line) => line.includes(`benchmark_id=${DATA.benchmark}`) && line.includes("horizons=1%2C2"))).toBe(true);
  });
});
