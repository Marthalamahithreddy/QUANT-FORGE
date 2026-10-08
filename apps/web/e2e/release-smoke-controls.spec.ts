import { spawn } from "node:child_process";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

/**
 * Failure injection for the release smoke's BROWSER phase (scripts/validation/real_smoke.mjs), run in a
 * real Chrome against a tiny stub site -- not the product. Each scenario serves the nine smoke pages
 * (valid, accessible, populated) with ONE defect injected into ONE page kind, runs the unmodified browser
 * script against it, and asserts the exit code and the named failure. The control scenarios prove the
 * stub is itself a PASSING site, so a failure is caused by the defect and nothing else.
 *
 * What these prove about the smoke: it fails (exit 1, naming the page) on an application error state
 * served with HTTP 200, on missing required data, on horizontal overflow, an accessibility violation,
 * script and console errors, an unexpected 5xx, a non-200 page, a write request from the browser, and on
 * an unreachable site; and that it does NOT fail on legitimate refusals and empty states. That the
 * markers it keys on are the ones the real components render is proven separately by
 * tests/page-health.test.tsx, which renders the real components.
 */
const SCRIPT = join(process.cwd(), "..", "..", "scripts", "validation", "real_smoke.mjs");

const EVENT = "11111111-2222-4333-8444-555555555555";
const FILING = "0000000000-26-000001";
const COMPANY = "Acme Corporation";
const FILLER = "Stored evidence, shown as the backend reported it, with the context a reader needs. ";

const ROW = "<table><caption>Stored rows</caption><thead><tr><th>Name</th></tr></thead><tbody><tr><td>row one</td></tr></tbody></table>";
const POSITION = (n: number) => `<p data-testid="pagination-position">Rows 1–${n} of ${n} stored items</p>`;

type Defect =
  | "alert-unavailable"
  | "alert-database"
  | "alert-invalid"
  | "alert-render"
  | "error-state"
  | "empty-where-populated"
  | "overflow"
  | "axe"
  | "script-error"
  | "console-error"
  | "browser-post"
  | "subresource-500"
  | "page-404"
  | "chart-missing"
  | "legit-states";

/** Which page each defect lands on, and how it appears. */
const TARGET: Record<Defect, "earnings-event" | "earnings" | "filings" | "overview" | "saved-record" | "research" | "filing-detail"> = {
  "alert-unavailable": "earnings-event",
  "alert-database": "research",
  "alert-invalid": "filing-detail",
  "alert-render": "saved-record",
  "error-state": "overview",
  "empty-where-populated": "earnings",
  overflow: "filings",
  axe: "overview",
  "script-error": "saved-record",
  "console-error": "earnings-event",
  "browser-post": "saved-record",
  "subresource-500": "research",
  "page-404": "filing-detail",
  "chart-missing": "research",
  "legit-states": "research",
};

function page(kind: string, defect?: Defect, noReturn = false): { status: number; html: string } {
  const injected = defect && TARGET[defect] === kind ? defect : undefined;
  const h = (h1: string, h2: string[], body = "") =>
    `<h1>${h1}</h1><p>${FILLER}</p>${h2.map((title) => `<section><h2>${title}</h2><p>${FILLER}</p></section>`).join("")}${body}`;
  let body: string;
  switch (kind) {
    case "overview":
      body = h("Overview", ["Latest earnings events", "System"], `${ROW}<span data-state="VALUE">Ready</span>`);
      break;
    case "earnings":
      body = h("Earnings", [], injected === "empty-where-populated" ? '<div role="status"><p>No earnings events are stored yet</p></div>' : `${ROW}${POSITION(1)}`);
      break;
    case "earnings-event":
      body = h(COMPANY, ["Stored event", "Market reaction", "Provenance"]);
      break;
    case "research":
      body = h(
        COMPANY,
        ["DESCRIPTIVE", "Refusal ledger", "Provenance"],
        noReturn || injected === "chart-missing" ? "" : '<svg role="img" aria-label="Horizon returns" width="40" height="20"><rect width="40" height="20" fill="#333"/></svg>',
      );
      break;
    case "filings":
      body = h("Filings", [], `${ROW}${POSITION(1)}`);
      break;
    case "filing-detail":
      body = h(FILING, ["Current EPS", "Comparative EPS", "EPS change"]);
      break;
    case "saved-list":
      body = h("Saved research", [], `${ROW}${POSITION(2)}`);
      break;
    default:
      body = h("Saved research record", ["Stored baseline identity", "Stored result", "Verification"]);
  }
  const extra: Record<string, string> = {
    "alert-unavailable": '<div role="alert"><p>Temporarily unavailable</p><p>The QuantForge API did not return this earnings event.</p></div>',
    "alert-database": '<div role="alert"><p>Temporarily unavailable</p><p>The database cannot be reached right now.</p></div>',
    "alert-invalid": '<div role="alert"><p>The API answered, but not in the expected form</p></div>',
    "alert-render": '<div role="alert"><h2>Something went wrong rendering this page</h2></div>',
    "error-state": '<span data-state="ERROR">Not ready</span>',
    overflow: '<div style="width:3000px;height:10px;background:#ccc"></div>',
    axe: '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="1" height="1">',
    "script-error": '<script>throw new Error("render failed")</script>',
    "console-error": '<script>console.error("Hydration failed")</script>',
    "browser-post": '<script>fetch("/api/v1/research/records",{method:"POST",body:"{}"}).catch(()=>{})</script>',
    "subresource-500": '<script>fetch("/boom").catch(()=>{})</script>',
    "legit-states": '<div role="status"><p>Point-in-time study: Refused. INELIGIBLE_NO_AVAILABILITY_EVIDENCE</p></div><span data-state="MISSING">Missing target price</span><span data-state="REFUSED">Refused</span><span data-state="UNAVAILABLE">Insufficient forward sessions</span>',
  };
  const status = injected === "page-404" ? 404 : 200;
  const addition = injected && injected in extra ? extra[injected] : "";
  return {
    status,
    html: `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="icon" href="data:,"><title>${kind} · QuantForge</title></head><body><main>${body}${addition}</main></body></html>`,
  };
}

function routeKind(pathname: string): string | undefined {
  if (pathname === "/") return "overview";
  if (pathname === "/earnings") return "earnings";
  if (pathname.startsWith("/earnings/")) return "earnings-event";
  if (pathname.startsWith("/research/events/")) return "research";
  if (pathname === "/filings") return "filings";
  if (pathname.startsWith("/filings/")) return "filing-detail";
  if (pathname === "/research/saved") return "saved-list";
  if (pathname.startsWith("/research/saved/")) return "saved-record";
  return undefined;
}

interface Site {
  readonly url: string;
  readonly server: Server;
  readonly methods: string[];
}

async function site(options: { defect?: Defect; apiStatus?: number; noReturn?: boolean } = {}): Promise<Site> {
  const methods: string[] = [];
  const server = createServer((request, response) => {
    methods.push(request.method ?? "?");
    const url = new URL(request.url ?? "/", "http://x");
    const send = (status: number, type: string, text: string) => {
      response.writeHead(status, { "content-type": type });
      response.end(text);
    };
    if (url.pathname === "/boom") return send(500, "application/json", '{"detail":"boom"}');
    if (url.pathname.startsWith("/api/v1/")) {
      if (request.method !== "GET") return send(405, "application/json", '{"detail":"Method Not Allowed"}');
      if (options.apiStatus && url.pathname.includes("earnings-events")) return send(options.apiStatus, "application/json", '{"detail":"down"}');
      if (url.pathname.startsWith("/api/v1/research/observations/")) {
        const status = options.noReturn ? "MISSING_TARGET_PRICE" : "AVAILABLE";
        return send(200, "application/json", JSON.stringify({ descriptive: { study: { stock_returns: { forward_returns: [{ horizon: 1, status }] } } } }));
      }
      const items: Record<string, unknown[]> = {
        "/api/v1/earnings-events": [{ id: EVENT, company: { display_name: COMPANY } }],
        "/api/v1/benchmarks": [{ id: "bench-1" }],
        "/api/v1/research/records": [{ record_id: "rec-1" }, { record_id: "rec-2" }],
        "/api/v1/filings": [{ accession: FILING, company_cik: "0000000001" }],
      };
      const list = items[url.pathname];
      if (!list) return send(404, "application/json", '{"detail":"Not Found"}');
      return send(200, "application/json", JSON.stringify({ items: list, total: list.length, limit: 1, offset: 0 }));
    }
    const kind = routeKind(url.pathname);
    if (!kind) return send(404, "text/html", "<!doctype html><title>x</title>");
    const { status, html } = page(kind, options.defect, options.noReturn);
    return send(status, "text/html", html);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`, server, methods };
}

function runBrowserPhase(webUrl: string, apiUrl = webUrl): Promise<{ code: number; out: string }> {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [SCRIPT], { env: { ...process.env, QF_WEB_URL: webUrl, QF_API_URL: apiUrl, REAL_SMOKE_VERIFY: "0" } });
    let out = "";
    child.stdout.on("data", (chunk) => (out += chunk));
    child.stderr.on("data", (chunk) => (out += chunk));
    child.on("close", (code) => resolve({ code: code ?? -1, out }));
  });
}

const failures = (out: string) => out.split("\n").filter((line) => line.startsWith("SMOKE FAIL "));

interface Scenario {
  readonly name: string;
  readonly defect?: Defect;
  readonly apiStatus?: number;
  /** The API reports no computed stock return: the research page legitimately has no chart. */
  readonly noReturn?: boolean;
  /** Substring every failure set must contain (the page and the reason). */
  readonly expect?: string[];
}

const SCENARIOS: readonly Scenario[] = [
  { name: "control: a healthy site passes" },
  { name: "control: legitimate refusals, missing and unavailable states still pass", defect: "legit-states" },
  { name: "control: an event with no computed stock return has no chart, and that is not a failure", noReturn: true },
  { name: "a chart missing although the API reports a computed return", defect: "chart-missing", expect: ["research-stock-only: expected a chart and found none"] },
  { name: "API unavailable: an alert error state served with HTTP 200", defect: "alert-unavailable", expect: ["earnings-event: application error state rendered with HTTP 200"] },
  { name: "database unavailable: an alert error state served with HTTP 200", defect: "alert-database", expect: ["research-stock-only: application error state rendered with HTTP 200", "research-spy: application error state rendered with HTTP 200"] },
  { name: "invalid response: a contract-failure error state", defect: "alert-invalid", expect: ["filing-detail: application error state rendered with HTTP 200"] },
  { name: "unexpected rendering error: the route error boundary", defect: "alert-render", expect: ["saved-record: application error state rendered with HTTP 200"] },
  { name: "an ERROR badge (data-state=ERROR) on the overview", defect: "error-state", expect: ['overview: application error state rendered with HTTP 200 (1 data-state="ERROR" marker'] },
  { name: "missing required data: an empty state where the API reports rows", defect: "empty-where-populated", expect: ["earnings: expected at least 1 row(s) because the API reports data, found 0"] },
  { name: "horizontal overflow", defect: "overflow", expect: ["filings: horizontal overflow"] },
  { name: "accessibility violation", defect: "axe", expect: ["overview: 1 axe violation(s): image-alt"] },
  { name: "uncaught script error", defect: "script-error", expect: ["saved-record: uncaught script error: render failed"] },
  { name: "console error", defect: "console-error", expect: ["earnings-event: console error: Hydration failed"] },
  { name: "a write request issued by the page", defect: "browser-post", expect: ["saved-record: the browser issued POST /api/v1/research/records"] },
  { name: "an unexpected 5xx while a page loads", defect: "subresource-500", expect: ["research-stock-only: 500 from /boom"] },
  { name: "a page that answers 404", defect: "page-404", expect: ["filing-detail: page answered 404, not 200"] },
  { name: "discovery: the API cannot list events", apiStatus: 503, expect: ["discovery: GET /earnings-events?limit=1 answered 503"] },
];

test.describe("release smoke browser phase: failure injection", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  const results = new Map<string, { code: number; out: string; methods: string[] }>();
  const sites: Site[] = [];

  test.beforeAll(async () => {
    test.setTimeout(240_000); // the hook's own budget: every scenario runs here
    // Several Chrome instances at once keep the whole group to about a minute.
    const queue = [...SCENARIOS];
    const worker = async () => {
      for (let scenario = queue.shift(); scenario; scenario = queue.shift()) {
        const stub = await site({ defect: scenario.defect, apiStatus: scenario.apiStatus, noReturn: scenario.noReturn });
        sites.push(stub);
        const outcome = await runBrowserPhase(stub.url);
        results.set(scenario.name, { ...outcome, methods: stub.methods });
      }
    };
    await Promise.all(Array.from({ length: 4 }, worker));
  });

  test.afterAll(() => {
    for (const stub of sites) stub.server.close();
  });

  for (const scenario of SCENARIOS) {
    test(scenario.name, () => {
      const result = results.get(scenario.name);
      expect(result, "scenario did not run").toBeDefined();
      const { code, out } = result!;
      if (!scenario.expect) {
        expect(failures(out), out).toEqual([]);
        expect(code).toBe(0);
        expect(out).toContain("SMOKE PASS 9 page(s) x 2 viewports");
        return;
      }
      expect(code).toBe(1);
      for (const fragment of scenario.expect) expect(failures(out).join("\n")).toContain(fragment);
    });
  }

  test("a healthy run's browser issued only GET requests, and says so", () => {
    const result = results.get("control: a healthy site passes")!;
    expect(result.out).toMatch(/SMOKE REQUESTS \{"GET":\d+\}/);
    expect(new Set(result.methods)).toEqual(new Set(["GET"]));
  });

  test("an unreachable site fails with a named navigation failure instead of passing", async () => {
    const stub = await site(); // the API (discovery) is alive; the browser is pointed at a closed port
    const dead = await runBrowserPhase("http://127.0.0.1:1", stub.url);
    stub.server.close();
    expect(dead.code).toBe(1);
    expect(failures(dead.out).join("\n")).toContain("navigation failed");
  });
});
