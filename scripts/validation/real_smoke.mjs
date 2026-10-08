// Real-data accessibility (axe-core) and responsive smoke over populated pages.
// Usage: node scripts/validation/real_smoke.mjs
//   QF_WEB_URL (default http://127.0.0.1:3101) must serve the production build against a backend at
//   QF_API_URL (default http://127.0.0.1:8000). Read-only: every page is a GET. The explicit-verify
//   page (?verify=1) is only visited with REAL_SMOKE_VERIFY=1, because it asks the backend to verify.
//
// Pass criteria (the exit code is 1 and every violation is listed if any fails):
//   * every page answers 200 (no redirect loop, no error page);
//   * no response the browser receives while loading is a 5xx;
//   * the page is a WORKING application page, not an error state served with 200 (page_health.mjs): no
//     role="alert" / data-state="ERROR" marker, no uncaught script error or console error, and every
//     page shows what the backend reported it holds (a list the API says has rows has rows). Expected
//     domain states (a PIT refusal, a missing target price, an honest empty list) are NOT failures;
//   * zero axe violations (WCAG 2 A/AA, 2.1 A/AA);
//   * no horizontal page overflow at 1440 px and at 390 px;
//   * the browser itself issued only GET requests (a mutation attempt is a failure).
// It is called by scripts/release-smoke.mjs, which owns the phases around it.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import path from "node:path";

import { inspectDocument, judgePage } from "./page_health.mjs";

const web = path.resolve(import.meta.dirname, "../../apps/web");
const require = createRequire(path.join(web, "package.json"));
const { chromium } = require("@playwright/test");
const axeSource = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");

const BASE = process.env.QF_WEB_URL ?? "http://127.0.0.1:3101";
const API = process.env.QF_API_URL ?? "http://127.0.0.1:8000";
/** A discovery read that cannot be trusted is a named failure, not a crash with a stack trace. */
async function getJson(route) {
  let response;
  try {
    response = await fetch(`${API}/api/v1${route}`, { signal: AbortSignal.timeout(15_000) });
  } catch (error) {
    throw new Failure(`discovery: GET ${route} failed (${error?.cause?.code ?? error?.name ?? "error"})`);
  }
  if (response.status !== 200) throw new Failure(`discovery: GET ${route} answered ${response.status}`);
  try {
    return await response.json();
  } catch {
    throw new Failure(`discovery: GET ${route} did not return JSON`);
  }
}
class Failure extends Error {}

// Identities and totals are discovered from the backend under test: the first stored event, benchmark,
// saved record and FSDS-backed filing. The totals decide what each page MUST show (populated, or
// legitimately empty): the page is judged against what the API reports, never against itself.
let events, benchmarks, records, filings, observation;
try {
  [events, benchmarks, records, filings] = [
    await getJson("/earnings-events?limit=1"),
    await getJson("/benchmarks?limit=1"),
    await getJson("/research/records?limit=1"),
    await getJson("/filings?limit=1"),
  ];
  if (events.items[0]?.id) observation = await getJson(`/research/observations/${events.items[0].id}`);
} catch (error) {
  if (!(error instanceof Failure)) throw error;
  console.log(`SMOKE FAIL ${error.message}`);
  process.exit(1);
}
const E = events.items[0]?.id;
const B = benchmarks.items[0]?.id;
const R = records.items[0]?.record_id;
const F = filings.items[0];
const company = events.items[0]?.company?.display_name ?? "";
if (!E || !F) {
  console.log("SMOKE FAIL discovery: the backend under test holds no event or no FSDS-backed filing");
  process.exit(1);
}
const rowsIf = (total) => Math.min(total, 1);
// A chart exists only when at least one horizon has a computed stock return; an event whose horizons are all
// missing or unprovable legitimately has none (the page then says so), so the chart is required only when the API reports a return.
const hasStockReturn = (observation?.descriptive?.study?.stock_returns?.forward_returns ?? []).some((entry) => entry.status === "AVAILABLE");
const RESEARCH = { h1: company, h2: ["DESCRIPTIVE", "Refusal ledger", "Provenance"], chart: hasStockReturn };
const RECORD = { h1: "Saved research record", h2: ["Stored baseline identity", "Stored result", "Verification"] };
// [name, url, what the backend reported this page must show]
const PAGES = [
  ["overview", "/", { h1: "Overview", h2: ["Latest earnings events", "System"], minRows: rowsIf(events.total), badges: [{ state: "VALUE", text: "Ready" }] }],
  ["earnings", "/earnings", { h1: "Earnings", minRows: rowsIf(events.total), positionTotal: events.total }],
  ["earnings-event", `/earnings/${E}`, { h1: company, h2: ["Stored event", "Market reaction", "Provenance"] }],
  ["research-stock-only", `/research/events/${E}`, RESEARCH],
  ...(B ? [["research-spy", `/research/events/${E}?benchmark_id=${B}&horizons=1,2,5`, RESEARCH]] : []),
  ["filings", "/filings", { h1: "Filings", minRows: rowsIf(filings.total), positionTotal: filings.total }],
  [
    "filing-detail",
    `/filings/${F.accession}?cik=${F.company_cik}&period_class=QUARTER&eps_kind=DILUTED`,
    { h1: F.accession, h2: ["Current EPS", "Comparative EPS", "EPS change"] },
  ],
  ["saved-list", "/research/saved", { h1: "Saved research", minRows: rowsIf(records.total), positionTotal: records.total }],
  ...(R ? [["saved-record", `/research/saved/${R}`, RECORD]] : []),
  ...(R && process.env.REAL_SMOKE_VERIFY === "1" ? [["saved-record-verified", `/research/saved/${R}?verify=1`, { ...RECORD, verification: true }]] : []),
];

const browser = await chromium.launch({ channel: "chrome" });
const out = [];
const failures = [];
const methods = {}; // every request the BROWSER issued, by method
const hasVerification = (snapshot) => snapshot.verifications.length > 0;
for (const [label, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
  const context = await browser.newContext({ viewport });
  for (const [name, url, expectation] of PAGES) {
    const page = await context.newPage();
    page.on("request", (request) => {
      methods[request.method()] = (methods[request.method()] ?? 0) + 1;
      if (request.method() !== "GET") failures.push(`${label} ${name}: the browser issued ${request.method()} ${new URL(request.url()).pathname}`);
    });
    // An uncaught script error or a console error means the page did not render as designed, even at 200.
    page.on("pageerror", (error) => failures.push(`${label} ${name}: uncaught script error: ${String(error.message).slice(0, 120)}`));
    page.on("console", (message) => {
      if (message.type() === "error") failures.push(`${label} ${name}: console error: ${message.text().slice(0, 120)}`);
    });
    page.on("response", (response) => {
      if (response.status() >= 500) failures.push(`${label} ${name}: ${response.status()} from ${new URL(response.url()).pathname}`);
    });
    let response = null;
    try {
      response = await page.goto(BASE + url, { waitUntil: "networkidle", timeout: 30_000 });
    } catch (error) {
      failures.push(`${label} ${name}: navigation failed (${String(error.message).split("\n")[0].slice(0, 100)})`);
      await page.close();
      continue;
    }
    if (!response || response.status() !== 200) failures.push(`${label} ${name}: page answered ${response?.status() ?? "nothing"}, not 200`);
    await page.waitForTimeout(500);
    const snapshot = await page.evaluate(`(${inspectDocument.toString()})(document)`);
    snapshot.verifications = await page.evaluate(() => [...document.querySelectorAll("[data-verification]")].map((el) => el.getAttribute("data-verification")));
    for (const problem of judgePage(snapshot, expectation)) failures.push(`${label} ${name}: ${problem}`);
    if (expectation.verification && !hasVerification(snapshot)) failures.push(`${label} ${name}: the explicit-verify page shows no verification result`);
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
    out.push({
      viewport: label,
      name,
      domainStates: snapshot.domainStates,
      verifications: snapshot.verifications,
      axe,
      ...layout,
    });
    await page.close();
  }
  await context.close();
}
await browser.close();
for (const row of out) console.log(JSON.stringify(row));
console.log(`SMOKE REQUESTS ${JSON.stringify(methods)}`);
if (failures.length > 0) {
  for (const failure of failures) console.log(`SMOKE FAIL ${failure}`);
  process.exitCode = 1;
} else {
  console.log(`SMOKE PASS ${PAGES.length} page(s) x 2 viewports`);
}
