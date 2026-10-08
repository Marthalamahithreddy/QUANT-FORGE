// Page-level performance of a running production build: server-rendered latency, the API requests each page
// makes, and what the browser downloads. Read-only: every request is a GET.
//
//   node scripts/validation/page_perf.mjs <web-url> <api-url> <api-access-log> [runs=15]
//
// <api-access-log> is the API's uvicorn log (log_level=info; start it with PYTHONUNBUFFERED=1 or the lines
// arrive late and the count is short), used ONLY to count the requests each page
// render caused: the log is read before and after one render and the GET lines in between are counted
// (the page is requested alone, so they are that page's). The browser section loads each page in Chrome
// and reports DOMContentLoaded / load, the number of requests and the bytes transferred (JS separately).
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import path from "node:path";

const [WEB, API, LOG, RUNS = "15"] = process.argv.slice(2);
if (!WEB || !API || !LOG) throw new Error("usage: page_perf.mjs <web-url> <api-url> <api-access-log> [runs]");
const runs = Number(RUNS);
const require = createRequire(path.resolve(import.meta.dirname, "../../apps/web/package.json"));
const { chromium } = require("@playwright/test");

const json = async (route) => (await fetch(`${API}/api/v1${route}`)).json();
const E = (await json("/earnings-events?limit=1")).items[0].id;
const B = (await json("/benchmarks?limit=1")).items[0].id;
const R = (await json("/research/records?limit=1")).items[0].record_id;
const F = (await json("/filings?limit=1")).items[0];
const PAGES = [
  ["overview", "/"],
  ["earnings list", "/earnings"],
  ["earnings list (page 2)", "/earnings?page=2"],
  ["earnings event", `/earnings/${E}`],
  ["research finder", "/research"],
  ["research (stock-only)", `/research/events/${E}`],
  ["research (benchmark, 3 horizons)", `/research/events/${E}?benchmark_id=${B}&horizons=1,2,5`],
  ["research (benchmark, 10 horizons)", `/research/events/${E}?benchmark_id=${B}&horizons=1,2,3,4,5,6,7,8,9,10`],
  ["filings list", "/filings"],
  ["filing detail", `/filings/${F.accession}?cik=${F.company_cik}&period_class=QUARTER&eps_kind=DILUTED`],
  ["saved list", "/research/saved"],
  ["saved record", `/research/saved/${R}`],
  ["saved record verified", `/research/saved/${R}?verify=1`],
];

const lines = () => readFileSync(LOG, "utf8").split("\n").filter(Boolean);
const apiRequestsSince = (start) =>
  lines()
    .slice(start)
    .filter((line) => /"GET \/(api\/v1\/|health)/.test(line)).length;
const percentile = (values, fraction) => [...values].sort((a, b) => a - b)[Math.min(values.length - 1, Math.round(fraction * (values.length - 1)))];
const median = (values) => percentile(values, 0.5);
const ms = (n) => Math.round(n * 10) / 10;

console.log(`web ${WEB} api ${API}; ${runs} warm renders per page (server-rendered HTML, full body read)`);
console.log(`${"page".padEnd(36)} ${"api".padStart(4)} ${"bytes".padStart(8)} ${"first".padStart(8)} ${"median".padStart(8)} ${"p95".padStart(8)} ${"max".padStart(8)}`);
for (const [name, route] of PAGES) {
  const before = lines().length;
  let t = performance.now();
  const first = await fetch(WEB + route);
  const body = await first.text();
  const firstMs = performance.now() - t;
  await new Promise((resolve) => setTimeout(resolve, 600));
  const apiCount = apiRequestsSince(before);
  const samples = [];
  for (let i = 0; i < runs; i += 1) {
    t = performance.now();
    await (await fetch(WEB + route)).text();
    samples.push(performance.now() - t);
  }
  const status = first.status === 200 ? "" : ` [HTTP ${first.status}]`;
  console.log(
    `${name.padEnd(36)} ${String(apiCount).padStart(4)} ${String(body.length).padStart(8)} ${String(ms(firstMs)).padStart(8)} ${String(ms(median(samples))).padStart(8)} ${String(ms(percentile(samples, 0.95))).padStart(8)} ${String(ms(Math.max(...samples))).padStart(8)}${status}`,
  );
}

console.log("\nbrowser (Chrome, desktop 1440x900, cold cache per page): requests / transferred KB (JS KB) / DOMContentLoaded ms / load ms");
const browser = await chromium.launch({ channel: "chrome" });
for (const [name, route] of PAGES) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  let requests = 0;
  let bytes = 0;
  let jsBytes = 0;
  page.on("response", async (response) => {
    requests += 1;
    try {
      const body = await response.body();
      bytes += body.length;
      if ((response.headers()["content-type"] ?? "").includes("javascript")) jsBytes += body.length;
    } catch {
      /* redirects have no body */
    }
  });
  await page.goto(WEB + route, { waitUntil: "load" });
  await page.waitForLoadState("networkidle");
  const timing = await page.evaluate(() => {
    const [nav] = performance.getEntriesByType("navigation");
    return { dcl: nav.domContentLoadedEventEnd, load: nav.loadEventEnd };
  });
  console.log(`${name.padEnd(36)} ${String(requests).padStart(3)} req ${String(Math.round(bytes / 1024)).padStart(6)} KB (js ${String(Math.round(jsBytes / 1024)).padStart(5)})  dcl ${String(ms(timing.dcl)).padStart(7)}  load ${String(ms(timing.load)).padStart(7)}`);
  await context.close();
}
await browser.close();
