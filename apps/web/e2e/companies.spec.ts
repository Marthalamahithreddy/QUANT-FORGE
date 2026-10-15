import { alerts, companies, expect, expectNoInternalDetail, noPageOverflow, settled, test } from "./support";

const byName = (needle: string) => {
  const found = companies.directory.items.find((item) => item.name.includes(needle));
  if (!found) throw new Error(`the capture has no company named ${needle}`);
  return found;
};
/** Stored companies whose newest filing has every metric AVAILABLE, and those where one is missing. */
const newest = (cik: string) => companies.financials[cik]!.filings[0]!;
const covered = (cik: string) => newest(cik).metrics.every((m) => m.current.status === "AVAILABLE");

test.describe("Companies", () => {
  test("opens with a curated Popular companies list that says what it is not, and distinguishes coverage", async ({ page, fixture }) => {
    await page.goto("/companies");
    await settled(page);
    await expect(page.getByRole("heading", { level: 1, name: "Companies" })).toBeVisible();

    const popular = page.getByRole("region", { name: "Popular companies" });
    await expect(popular).toContainText(/not live trending data/i);
    await expect(popular).toContainText(/not a recommendation/i);
    await expect(popular).toContainText(/alphabetical order by ticker, not a ranking/i);
    await expect(popular).not.toContainText(/trending now|top gainers|most traded|buy|sell/i);
    // Private companies are explained generically; none is named or given a ticker.
    await expect(popular).toContainText(/private companies have no such filings/i);

    const apple = byName("Apple");
    const nvidia = byName("Nvidia");
    expect(covered(apple.cik)).toBe(true);
    expect(covered(nvidia.cik)).toBe(false);
    await expect(popular.locator("article[data-coverage='FULL']").getByRole("link", { name: apple.name })).toBeVisible();
    const limited = popular.getByRole("article", { name: nvidia.name });
    await expect(limited).toHaveAttribute("data-coverage", "LIMITED");
    await expect(limited).toContainText("Limited coverage");
    // The missing figure is named, never a zero; the figures that exist are shown.
    const metrics = newest(nvidia.cik).metrics;
    await expect(limited.locator('[data-state="MISSING"]')).toHaveCount(metrics.filter((m) => m.current.status !== "AVAILABLE").length);
    await expect(limited.locator("[data-exact]")).toHaveCount(metrics.filter((m) => m.current.status === "AVAILABLE").length);
    await expect(limited).toContainText(/never\s+zero/);

    // One read per featured company plus the directory, all GET, none from a provider.
    expect(await fixture.served("/financials")).toHaveLength(10);
    await expect(alerts(page)).toHaveCount(0);
    await expectNoInternalDetail(page);
    await noPageOverflow(page);
  });

  test("searches for a supported company and opens its financial history with evidence", async ({ page, fixture }) => {
    const nvidia = byName("Nvidia");
    await page.goto("/companies");
    await settled(page);
    await page.getByRole("textbox", { name: "Name, ticker or CIK" }).fill(nvidia.tickers[0]!.toLowerCase());
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page).toHaveURL(new RegExp(`/companies\\?q=${nvidia.tickers[0]!.toLowerCase()}$`));
    await settled(page);

    // A search shows its results only: the curated list is not repeated beside them.
    await expect(page.getByRole("region", { name: "Popular companies" })).toHaveCount(0);
    const table = page.getByRole("table", { name: "Stored companies" });
    await expect(table.getByRole("row")).toHaveCount(2); // header + the one match
    await fixture.reset(); // from here on, count only what opening the company itself reads
    await table.getByRole("link", { name: nvidia.name }).click();

    await expect(page).toHaveURL(new RegExp(`/companies/${nvidia.cik}$`));
    await settled(page);
    await expect(page.getByRole("heading", { level: 1, name: nvidia.name })).toBeVisible();
    await expect(page.getByRole("note", { name: "Data boundary" })).toContainText(/no point-in-time availability claim/i);

    // Income and EPS are shown exactly as captured; revenue is a named refusal.
    const filing = newest(nvidia.cik);
    const income = filing.metrics.find((m) => m.metric === "NET_INCOME")!.current.fact!.value;
    await expect(page.getByRole("article", { name: "Net income (parent)" }).locator(`[data-exact="${income}"]`).first()).toBeVisible();
    const revenue = page.getByRole("article", { name: "Revenue (customer contracts)" });
    await expect(revenue.locator('[data-state="MISSING"]')).not.toHaveCount(0);
    await expect(revenue.locator("[data-exact]")).toHaveCount(0);

    // Evidence for the newest filing: accession, the package, the SHA-256 and the SEC link.
    const evidence = page.locator(`#evidence-${filing.accession}`);
    await expect(evidence.getByRole("link", { name: filing.accession })).toBeVisible();
    await expect(evidence).toContainText("SEC Financial Statement Data Sets package");
    await expect(evidence.getByRole("link", { name: /Open the filing index on sec.gov/ })).toHaveAttribute("href", /^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\//);
    await expect(evidence.getByRole("table", { name: /Stored facts behind/ })).toBeVisible();
    expect(await fixture.served(`/api/v1/companies/${nvidia.cik}/financials`)).toHaveLength(1);
    await expect(alerts(page)).toHaveCount(0);
    await expectNoInternalDetail(page);
    await noPageOverflow(page);
  });

  test("a fully covered company shows its history: every stored filing, quarters and the annual separately", async ({ page }) => {
    const apple = byName("Apple");
    await page.goto("/companies");
    await settled(page);
    await page.getByRole("region", { name: "Popular companies" }).getByRole("link", { name: apple.name }).click();
    await expect(page).toHaveURL(new RegExp(`/companies/${apple.cik}$`));
    await settled(page);

    const filings = companies.financials[apple.cik]!.filings;
    const table = page.getByRole("table", { name: /per stored filing/ });
    await expect(table.getByRole("row")).toHaveCount(filings.length + 1);
    for (const form of new Set(filings.map((f) => f.form))) await expect(table.getByText(form, { exact: true }).first()).toBeVisible();
    // Every figure in the newest filing's cards is a captured exact value, never a derived or default one.
    const exact = newest(apple.cik).metrics.map((m) => m.current.fact!.value);
    for (const value of exact) await expect(page.locator(`[data-exact="${value}"], .num:text-is("${value}")`).first()).toBeAttached();
    await expect(page.getByRole("region", { name: /by quarter/ }).first()).toBeVisible();
    await expect(alerts(page)).toHaveCount(0);
  });

  test("a company that cannot be read is shown as unavailable, with no figure", async ({ page, fixture }) => {
    const amd = byName("Advanced Micro");
    await fixture.fault({ match: `/api/v1/companies/${amd.cik}/financials`, status: 404, body: { detail: "No company is stored under this CIK." } });
    await page.goto("/companies");
    await settled(page);
    const card = page.getByRole("article", { name: amd.tickers[0]! });
    await expect(card).toHaveAttribute("data-coverage", "UNAVAILABLE");
    await expect(card).toContainText(/Not stored in this workspace/);
    await expect(card.locator("[data-exact]")).toHaveCount(0);
    // The others are unaffected.
    await expect(page.getByRole("article", { name: byName("Apple").name })).toHaveAttribute("data-coverage", "FULL");
    await expect(alerts(page)).toHaveCount(0);
  });

  test("an unknown company is a not-found page and an unmatched search is an honest empty list", async ({ page }) => {
    await page.goto(`/companies/${companies.unknown_company.cik}`);
    await settled(page);
    await expect(page.getByRole("heading", { level: 1, name: "No stored company has this CIK" })).toBeVisible();
    await expect(page.locator("main [data-exact]")).toHaveCount(0);
    await expect(page.locator("main")).toContainText(/Nothing was looked up elsewhere/);

    await page.goto("/companies?q=zzzz-no-such-company");
    await settled(page);
    await expect(page.getByText("No stored company matches this search")).toBeVisible();
    await expect(page.getByRole("region", { name: "Popular companies" })).toHaveCount(0);
    await expect(page.locator("main [data-exact]")).toHaveCount(0);
    await expectNoInternalDetail(page);
  });
});
