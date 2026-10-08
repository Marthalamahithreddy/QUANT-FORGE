// Real-data accessibility (axe-core) and responsive smoke over populated pages.
// Usage: node scripts/validation/real_smoke.mjs
//   QF_WEB_URL (default http://127.0.0.1:3101) must serve the production build against a backend at
//   QF_API_URL (default http://127.0.0.1:8000). Read-only: every page is a GET. The explicit-verify
//   page (?verify=1) is only visited with REAL_SMOKE_VERIFY=1, because it asks the backend to verify.
//
// Pass criteria (the exit code is 1 and every violation is listed if any fails):
//   * every page answers 200 (no redirect loop, no error page);
//   * no response the browser receives while loading is a 5xx;
//   * zero axe violations (WCAG 2 A/AA, 2.1 A/AA);
//   * no horizontal page overflow at 1440 px and at 390 px.
// It is called by scripts/release-smoke.mjs, which owns the phases around it.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import path from "node:path";

const web = path.resolve(import.meta.dirname, "../../apps/web");
const require = createRequire(path.join(web, "package.json"));
const { chromium } = require("@playwright/test");
const axeSource = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");

const BASE = process.env.QF_WEB_URL ?? "http://127.0.0.1:3101";
const API = process.env.QF_API_URL ?? "http://127.0.0.1:8000";
const getJson = async (route) => (await fetch(`${API}/api/v1${route}`)).json();
// Identities are discovered from the backend under test: the first stored event, benchmark, saved
// record and FSDS-backed filing.
const E = (await getJson("/earnings-events?limit=1")).items[0]?.id;
const B = (await getJson("/benchmarks?limit=1")).items[0]?.id;
const R = (await getJson("/research/records?limit=1")).items[0]?.record_id;
const F = (await getJson("/filings?limit=1")).items[0];
if (!E || !F) throw new Error("the backend under test holds no event or no FSDS-backed filing");
const PAGES = [
  ["overview", "/"],
  ["earnings", "/earnings"],
  ["earnings-event", `/earnings/${E}`],
  ["research-stock-only", `/research/events/${E}`],
  ...(B ? [["research-spy", `/research/events/${E}?benchmark_id=${B}&horizons=1,2,5`]] : []),
  ["filings", "/filings"],
  ["filing-detail", `/filings/${F.accession}?cik=${F.company_cik}&period_class=QUARTER&eps_kind=DILUTED`],
  ["saved-list", "/research/saved"],
  ...(R ? [["saved-record", `/research/saved/${R}`]] : []),
  ...(R && process.env.REAL_SMOKE_VERIFY === "1" ? [["saved-record-verified", `/research/saved/${R}?verify=1`]] : []),
];

const browser = await chromium.launch({ channel: "chrome" });
const out = [];
const failures = [];
for (const [label, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
  const context = await browser.newContext({ viewport });
  for (const [name, url] of PAGES) {
    const page = await context.newPage();
    page.on("response", (response) => {
      if (response.status() >= 500) failures.push(`${label} ${name}: ${response.status()} from ${new URL(response.url()).pathname}`);
    });
    const response = await page.goto(BASE + url, { waitUntil: "networkidle" });
    if (!response || response.status() !== 200) failures.push(`${label} ${name}: page answered ${response?.status() ?? "nothing"}, not 200`);
    await page.waitForTimeout(500);
    await page.evaluate(axeSource);
    const axe = await page.evaluate(async () => {
      const r = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] });
      return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }));
    });
    const layout = await page.evaluate(() => {
      const doc = document.documentElement;
      const wide = [...document.querySelectorAll("main *")].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.right > window.innerWidth + 1 && !el.closest("[class*=overflow], [role=region]");
      }).slice(0, 5).map((el) => el.tagName + "." + String(el.className).slice(0, 40));
      return { pageOverflow: doc.scrollWidth > window.innerWidth + 1, scrollWidth: doc.scrollWidth, innerWidth: window.innerWidth, wide };
    });
    if (axe.length > 0) failures.push(`${label} ${name}: ${axe.length} axe violation(s): ${axe.map((v) => v.id).join(", ")}`);
    if (layout.pageOverflow) failures.push(`${label} ${name}: horizontal overflow (${layout.scrollWidth} > ${layout.innerWidth})`);
    out.push({ viewport: label, name, axe, ...layout });
    await page.close();
  }
  await context.close();
}
await browser.close();
for (const row of out) console.log(JSON.stringify(row));
if (failures.length > 0) {
  for (const failure of failures) console.log(`SMOKE FAIL ${failure}`);
  process.exitCode = 1;
} else {
  console.log(`SMOKE PASS ${PAGES.length} page(s) x 2 viewports`);
}
