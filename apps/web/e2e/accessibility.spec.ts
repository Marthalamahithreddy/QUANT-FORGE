import { readFileSync } from "node:fs";

import type { Page } from "@playwright/test";

import { DATA, companies, expect, settled, test } from "./support";

/**
 * Automated checks in a REAL browser, including colour contrast, which jsdom cannot measure. They
 * find only what axe can find (about a third of accessibility problems); they are not a WCAG
 * conformance claim and do not replace the keyboard and responsive journeys.
 */
const AXE = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

async function violations(page: Page) {
  await page.evaluate(AXE);
  return page.evaluate(async (tags) => {
    // @ts-expect-error axe is injected above
    const result = await axe.run(document, { runOnly: { type: "tag", values: tags } });
    return result.violations.map((v: { id: string; impact: string; nodes: { target: unknown[]; html: string }[] }) => ({
      rule: v.id,
      impact: v.impact,
      nodes: v.nodes.slice(0, 3).map((n) => `${n.target.join(" ")} :: ${n.html.slice(0, 120)}`),
    }));
  }, TAGS);
}

const complete = DATA.record("complete_benchmark");
const partial = DATA.record("partial_benchmark");
const changed = DATA.record("manifest_changed");
const resultChanged = DATA.record("result_changed");

const cikOf = (name: string) => companies.directory.items.find((item) => item.name.includes(name))!.cik;

const PAGES: [string, string][] = [
  ["Overview", "/"],
  ["Companies with the Popular list", "/companies"],
  ["Companies search results", "/companies?q=nvda"],
  ["Company with full coverage", `/companies/${cikOf("Apple")}`],
  ["Company with limited coverage", `/companies/${cikOf("Nvidia")}`],
  ["Unknown company", `/companies/${companies.unknown_company.cik}`],
  ["Earnings list", "/earnings"],
  ["Earnings event", `/earnings/${DATA.event}`],
  ["Filings list", "/filings"],
  ["Filing", `/filings/${DATA.filing}?period_class=QUARTER&eps_kind=DILUTED`],
  ["Research finder", "/research"],
  ["Research default (PIT refused)", `/research/events/${DATA.event}`],
  ["Research with benchmark", `/research/events/${DATA.event}?horizons=1,2&benchmark_id=${DATA.benchmark}`],
  ["Research with filing", `/research/events/${DATA.event}?horizons=1,2&benchmark_id=${DATA.benchmark}&filing_accession=${DATA.filing}&eps_period_class=QUARTER&eps_kind=DILUTED`],
  ["Saved research list", "/research/saved"],
  ["Stored baseline COMPLETE", `/research/saved/${complete.id}`],
  ["Stored baseline PARTIAL", `/research/saved/${partial.id}`],
  ["Verification MATCH", `/research/saved/${complete.id}?verify=1`],
  ["Verification MANIFEST_CHANGED", `/research/saved/${changed.id}?verify=1`],
  ["Verification RESULT_CHANGED", `/research/saved/${resultChanged.id}?verify=1`],
  ["Not found", "/no-such-page"],
];

for (const theme of ["dark", "light"] as const) {
  test.describe(`axe in Chrome, ${theme} theme`, () => {
    test.beforeEach(async ({ context }) => {
      await context.addCookies([{ name: "qf-theme", value: theme, url: "http://127.0.0.1:3100" }]);
    });

    for (const [name, path] of PAGES) {
      test(name, async ({ page }) => {
        await page.goto(path);
        await settled(page);
        expect(await violations(page)).toEqual([]);
      });
    }

    test("an open evidence drawer", async ({ page }) => {
      await page.goto(`/research/events/${DATA.event}`);
      await page.getByRole("button", { name: /^Evidence/ }).first().click();
      await expect(page.getByRole("dialog")).toBeVisible();
      expect(await violations(page)).toEqual([]);
    });
  });
}

test.describe("axe at a narrow viewport", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  for (const [name, path] of [PAGES[0]!, PAGES[9]!, PAGES[10]!, PAGES[13]!] as [string, string][]) {
    test(name, async ({ page }) => {
      await page.goto(path);
      await settled(page);
      expect(await violations(page)).toEqual([]);
    });
  }
  test("the open navigation drawer", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("dialog", { name: "Navigation" })).toBeVisible();
    expect(await violations(page)).toEqual([]);
  });
});
