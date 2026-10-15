/**
 * A fixture-backed stand-in for the QuantForge API, for browser end-to-end tests ONLY.
 *
 * It serves the real backend responses captured under `tests/contract/` (earnings.json,
 * observation.json, vertical.json, records.json, companies.json), byte for byte. Nothing is simulated, generated or
 * hand-edited: a request that no capture answers is refused with 501, so a test never silently passes
 * on an invented response. The only composition is the one a list needs: the earnings index is the
 * set of captured event records ordered newest event date first, and the benchmark index is the union
 * of the two captured benchmark lists.
 *
 * This is NOT a live-backend test. Every claim made against it is a claim about the frontend given
 * those captured answers. The live seeded-backend smoke test (`e2e/live-smoke.spec.ts`) is separate.
 *
 * Test control (never reachable from the application, which only issues GET to /api/v1):
 *   POST /__e2e/reset            clears faults and the request log
 *   POST /__e2e/fault            {match, status?, body?, delayMs?}: persistent until reset; the first
 *                                fault whose `match` is contained in the request path is applied
 *   GET  /__e2e/log              the requests served since the last reset ("GET /path?query")
 */

import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.QF_FIXTURE_PORT ?? 8765);
const CONTRACT = join(dirname(fileURLToPath(import.meta.url)), "..", "tests", "contract");
const load = (name) => JSON.parse(readFileSync(join(CONTRACT, name), "utf8"));

const earnings = load("earnings.json");
const observation = load("observation.json");
const vertical = load("vertical.json");
const records = load("records.json");
const companies = load("companies.json");

// ── events: every captured event record, by id ────────────────────────────────────────────
const events = new Map(); // id -> { status, body }
const addEvent = (body, status = 200) => {
  if (body && typeof body === "object" && body.id) events.set(body.id, { status, body });
};
for (const item of earnings.page.items) addEvent(item);
addEvent(earnings.event);
for (const entry of observation.cases) addEvent(entry.event_record);
for (const entry of records.records) {
  // The record's event as the backend answered AFTER the whole capture (a deleted event is a 404).
  const reply = entry.event_record_after ?? entry.event_record;
  if (reply.status === 200) addEvent(reply.body);
  else events.set(entry.event_id, reply);
}

// The index lists the events a person would browse: the observation and seeded ones, newest first.
const browsable = [...earnings.page.items, ...observation.cases.map((entry) => entry.event_record)];
const seen = new Set();
const index = browsable
  .filter((event) => !seen.has(event.id) && seen.add(event.id))
  .sort((a, b) => (a.event_date === b.event_date ? a.id.localeCompare(b.id) : a.event_date < b.event_date ? 1 : -1));

// ── observations: keyed by event and the exact parameter set the capture used ─────────────
const OBS_KEYS = ["horizons", "benchmark_id", "security_id", "filing_accession", "eps_period_class", "eps_kind"];
const obsKey = (eventId, params) => `${eventId}|${JSON.stringify(OBS_KEYS.filter((k) => params[k] !== undefined).map((k) => [k, params[k]]))}`;
const observations = new Map();
for (const entry of observation.cases) observations.set(obsKey(entry.event_id, entry.params), { status: 200, body: entry.response });
for (const entry of records.records) {
  if (entry.rerun_observation) {
    const { params, status, body } = entry.rerun_observation;
    observations.set(obsKey(entry.event_id, params), { status, body });
  }
}

// ── benchmarks: the union of the two captured lists ───────────────────────────────────────
const benchmarks = (() => {
  const items = [];
  for (const list of [observation.benchmarks, records.benchmarks.body]) {
    for (const item of list.items) if (!items.some((known) => known.id === item.id)) items.push(item);
  }
  return items;
})();

// ── filings ───────────────────────────────────────────────────────────────────────────────
const epsKey = (accession, periodClass, kind, cik) => `${accession}|${periodClass}|${kind}|${cik ?? ""}`;
const epsAnswers = new Map(vertical.eps.map((entry) => [epsKey(entry.request.accession, entry.request.period_class, entry.request.eps_kind, entry.request.cik), entry.response]));

// ── companies: the captured directory and each captured company's financials ──────────────
// The directory search is the one composition a list needs: the captured full directory filtered the way
// the API documents it (name substring, exact current ticker or CIK, case-insensitive), then paged.
const matchesCompany = (item, q) => {
  const needle = q.toLowerCase();
  return item.name.toLowerCase().includes(needle) || item.tickers.some((t) => t.toLowerCase() === needle) || item.cik === q;
};

// ── records ───────────────────────────────────────────────────────────────────────────────
const recordById = new Map(records.records.map((entry) => [entry.record_id, entry]));

// ── control state ─────────────────────────────────────────────────────────────────────────
let faults = [];
let log = [];

const page = (items, query) => {
  const limit = Number(query.get("limit") ?? 50);
  const offset = Number(query.get("offset") ?? 0);
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset };
};

function answer(pathname, query) {
  // Liveness lives at /health on the real API (and the release smoke probes it there); the /api/v1
  // alias is what the application's own status indicator reads.
  if (pathname === "/health" || pathname === "/api/v1/health") return { status: 200, body: earnings.health };
  if (pathname === "/api/v1/health/ready") return { status: 200, body: earnings.ready };
  if (pathname === "/api/v1/benchmarks") return { status: 200, body: page(benchmarks, query) };

  if (pathname === "/api/v1/earnings-events") {
    let items = index;
    const ticker = query.get("ticker");
    if (ticker) items = items.filter((event) => event.resolved_tickers.includes(ticker));
    if (query.get("date_from")) items = items.filter((event) => event.event_date >= query.get("date_from"));
    if (query.get("date_to")) items = items.filter((event) => event.event_date <= query.get("date_to"));
    return { status: 200, body: page(items, query) };
  }
  if (pathname.startsWith("/api/v1/earnings-events/")) {
    return events.get(pathname.split("/")[4]) ?? { status: 404, body: observation.unknown_event.body };
  }

  if (pathname.startsWith("/api/v1/research/observations/")) {
    const eventId = pathname.split("/")[5];
    const params = Object.fromEntries(query.entries());
    const found = observations.get(obsKey(eventId, params));
    if (found) return found;
    if (!events.has(eventId)) return { status: 404, body: observation.unknown_event.body };
    return { status: 501, body: { detail: "E2E fixture: no captured response for this observation request." } };
  }

  if (pathname === "/api/v1/companies") {
    const q = query.get("q");
    return { status: 200, body: page(q ? companies.directory.items.filter((item) => matchesCompany(item, q)) : companies.directory.items, query) };
  }
  const company = /^\/api\/v1\/companies\/([^/]+)\/financials$/.exec(pathname);
  if (company) {
    const cik = decodeURIComponent(company[1]);
    const stored = companies.financials[cik];
    return stored ? { status: 200, body: stored } : { status: companies.unknown_company.status, body: companies.unknown_company.body };
  }

  if (pathname === "/api/v1/filings") {
    let items = vertical.index.items;
    if (query.get("form")) items = items.filter((filing) => filing.form === query.get("form"));
    if (query.get("cik")) items = items.filter((filing) => filing.company_cik === query.get("cik"));
    return { status: 200, body: page(items, query) };
  }
  const filing = /^\/api\/v1\/filings\/([^/]+)\/eps$/.exec(pathname);
  if (filing) {
    const found = epsAnswers.get(epsKey(decodeURIComponent(filing[1]), query.get("period_class"), query.get("eps_kind"), query.get("cik")));
    return found ? { status: 200, body: found } : { status: 501, body: { detail: "E2E fixture: no captured response for this filing request." } };
  }

  if (pathname === "/api/v1/research/records") return { status: 200, body: page(records.lists.limit_20.body.items, query) };
  const record = /^\/api\/v1\/research\/records\/([^/]+)(\/verify)?$/.exec(pathname);
  if (record) {
    const entry = recordById.get(record[1]);
    if (!entry) return { status: 404, body: records.unknown_record.detail.body };
    const reply = record[2] ? entry.verify : (entry.detail_after ?? entry.detail);
    return reply ?? { status: 501, body: { detail: "E2E fixture: no captured response for this record request." } };
  }
  return { status: 404, body: { detail: "Not Found" } };
}

const send = (response, status, body) => {
  const text = JSON.stringify(body);
  response.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(text) });
  response.end(text);
};

const readBody = (request) =>
  new Promise((resolve) => {
    let text = "";
    request.on("data", (chunk) => (text += chunk));
    request.on("end", () => resolve(text ? JSON.parse(text) : {}));
  });

createServer(async (request, response) => {
  const url = new URL(request.url, "http://fixture");

  if (url.pathname.startsWith("/__e2e/")) {
    if (url.pathname === "/__e2e/reset" && request.method === "POST") {
      faults = [];
      log = [];
      return send(response, 200, { ok: true });
    }
    if (url.pathname === "/__e2e/fault" && request.method === "POST") {
      faults.push(await readBody(request));
      return send(response, 200, { ok: true });
    }
    if (url.pathname === "/__e2e/log" && request.method === "GET") return send(response, 200, log);
    return send(response, 404, { detail: "unknown control request" });
  }

  log.push(`${request.method} ${url.pathname}${url.search}`);
  if (request.method !== "GET") return send(response, 405, { detail: "Method Not Allowed" });

  const fault = faults.find((entry) => url.pathname.includes(entry.match));
  if (fault?.delayMs) await new Promise((resolve) => setTimeout(resolve, fault.delayMs));
  if (fault && fault.status !== undefined) return send(response, fault.status, fault.body ?? { detail: "E2E injected fault." });

  const { status, body } = answer(url.pathname, url.searchParams);
  return send(response, status, body);
}).listen(PORT, "127.0.0.1", () => console.log(`fixture API on http://127.0.0.1:${PORT}`));
