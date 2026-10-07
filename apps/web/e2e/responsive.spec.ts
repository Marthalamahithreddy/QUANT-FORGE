import { DATA, expect, noPageOverflow, settled, test } from "./support";

/** The routes and states the product has, each checked at the four review widths in both themes. */
const complete = DATA.record("complete_benchmark");
const ROUTES: [string, string][] = [
  ["Overview", "/"],
  ["Earnings", "/earnings"],
  ["Earnings event", `/earnings/${DATA.event}`],
  ["Filings", "/filings"],
  ["Filing", `/filings/${DATA.filing}?period_class=QUARTER&eps_kind=DILUTED`],
  ["Research finder", "/research"],
  ["Research (PIT refused)", `/research/events/${DATA.event}`],
  ["Research (benchmark, filing)", `/research/events/${DATA.event}?horizons=1,2&benchmark_id=${DATA.benchmark}&filing_accession=${DATA.filing}&eps_period_class=QUARTER&eps_kind=DILUTED`],
  ["Saved research", "/research/saved"],
  ["Stored baseline", `/research/saved/${complete.id}`],
  ["Verification MATCH", `/research/saved/${complete.id}?verify=1`],
  ["Verification MANIFEST_CHANGED", `/research/saved/${DATA.record("manifest_changed").id}?verify=1`],
  ["Integrity failure", `/research/saved/${DATA.record("corrupt_digest").id}`],
  ["Not found", "/no-such-page"],
];

for (const width of [1440, 1024, 390, 320]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height: width >= 1024 ? 900 : 800 } });

    for (const theme of ["dark", "light"] as const) {
      for (const [name, path] of ROUTES) {
        test(`${theme}: ${name}`, async ({ page, context }) => {
          await context.addCookies([{ name: "qf-theme", value: theme, url: "http://127.0.0.1:3100" }]);
          await page.goto(path);
          await settled(page);
          await expect(page.locator("main")).toBeVisible();

          // No sideways scrolling of the page itself.
          await noPageOverflow(page);

          // Anything that DOES scroll sideways is a keyboard-focusable region with a name: dense tables
          // scroll in place and are never turned into cards or clipped.
          const strays = await page.evaluate(() =>
            [...document.querySelectorAll<HTMLElement>("main *")]
              .filter((node) => {
                const overflowX = getComputedStyle(node).overflowX;
                return (overflowX === "auto" || overflowX === "scroll") && node.scrollWidth > node.clientWidth + 1;
              })
              .filter((node) => !(node.getAttribute("role") === "region" && node.tabIndex === 0 && !!node.getAttribute("aria-label")))
              .map((node) => `${node.tagName.toLowerCase()}.${String(node.className).slice(0, 60)}`),
          );
          expect(strays, "a sideways scroller that is not a named, focusable region").toEqual([]);
        });
      }
    }
  });
}
