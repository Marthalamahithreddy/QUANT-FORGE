#!/usr/bin/env node
// Release smoke: "is this deployment serving the product correctly?" -- read-only, no providers.
//
//   node scripts/release-smoke.mjs [--verify] [--build] [--start-web] [--no-browser] [--db-fingerprint]
//
// Targets (environment):
//   QF_API_URL  the backend under test           (default http://127.0.0.1:8000)
//   QF_WEB_URL  the PRODUCTION web build serving it (default http://127.0.0.1:3101)
//
// Phases, in order. The first failure stops the run, names the phase on one line and exits 1:
//   1 liveness   GET /health                    -> 200, status "ok"
//   2 readiness  GET /api/v1/health/ready       -> 200, status "ready", database ok, schema compatible
//                                                  (Redis is reported but never gates, as in the API)
//   3 web        GET <web>/                     -> 200 from the production server
//   4 browser    scripts/validation/real_smoke.mjs: every page 200, no 5xx, NO application error state
//                rendered with 200 (role="alert" / data-state="ERROR", script or console errors), every page
//                shows what the API reported it holds, zero axe violations, no horizontal overflow at 1440
//                and 390 px, and the browser itself issued only GET requests
//   5 unchanged  the stored state is the same after the run as before it (see below) and readiness still holds
//
// What it never does: save a research record, ingest or call a provider, touch accounting data or run
// a migration. Every request this script makes is a GET. The explicit-verify page asks the backend to
// re-verify a saved record (itself read-only) and is only visited with --verify.
//
// "Unchanged" compares, before and after the browser phase:
//   * HTTP (always): for earnings events, filings, benchmarks and saved research records the total and a
//     SHA-256 over the canonical JSON of every item (up to 500 per list; saved records carry their
//     manifest and result fingerprints); the EPS facts of the first 5 filings; and one research
//     observation (the stored market bars and returns it is computed from).
//   * database (--db-fingerprint): scripts/validation/state_fingerprint.py -- per table the row count,
//     PostgreSQL's insert/update/delete counters, head/tail row digests and key digest; raw-artifact
//     references, ingestion runs and saved records in full. Read-only. Needs the application's Python.
// PROVES: no list the product serves, no saved record and (with --db-fingerprint) no table was written
// during the run. DOES NOT PROVE: that a value outside the digested windows of a very large table is
// unchanged beyond what the write counters say; nor anything about state before the run began.
//
// Flags: --build runs `npm run build` in apps/web first; --start-web serves that build with
// `next start` on the QF_WEB_URL port for the duration of the run (otherwise a server must already be
// running); --no-browser stops after phase 3 (the API/web checks only); --verify adds the verify page.
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const WEB_DIR = path.join(ROOT, "apps", "web");
const API = (process.env.QF_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const WEB = (process.env.QF_WEB_URL ?? "http://127.0.0.1:3101").replace(/\/+$/, "");
const args = new Set(process.argv.slice(2));
const TIMEOUT_MS = 15_000;
const AUDIT = { api: 0, web: 0 }; // every request THIS script issued; all of them are GET

class PhaseFailure extends Error {
  constructor(phase, message) {
    super(message);
    this.phase = phase;
  }
}

/**
 * The only network primitive in this file: a GET with a deadline. A GET is idempotent, so a dropped
 * connection (a pooled keep-alive socket the server closed while the browser phase ran) is retried
 * once on a fresh connection instead of being mistaken for a failed check.
 */
async function get(url, attempts = 2) {
  let last = { status: 0, json: null, error: "error" };
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      AUDIT[url.startsWith(WEB) ? "web" : "api"] += 1;
      const response = await fetch(url, {
        method: "GET",
        headers: { connection: "close" },
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
      const text = await response.text();
      let json = null;
      try {
        json = JSON.parse(text);
      } catch {
        /* not JSON (a page): fine */
      }
      return { status: response.status, json };
    } catch (error) {
      last = { status: 0, json: null, error: error?.cause?.code ?? error?.name ?? "error" };
    }
  }
  return last;
}

const ok = (phase, detail) => console.log(`[ok]   ${phase}${detail ? `  ${detail}` : ""}`);

async function liveness() {
  const { status, json, error } = await get(`${API}/health`);
  if (status !== 200 || json?.status !== "ok") {
    throw new PhaseFailure("liveness", `GET /health answered ${status || error || "nothing"}`);
  }
  ok("liveness", `${json.service} ${json.version}`);
}

async function readiness() {
  const { status, json, error } = await get(`${API}/api/v1/health/ready`);
  if (status === 0) throw new PhaseFailure("readiness", `GET /api/v1/health/ready: ${error}`);
  if (status !== 200 || json?.status !== "ready") {
    const why = json
      ? `database ${json.dependencies?.database?.state}, schema ${json.schema?.state}` +
        (json.schema?.current ? ` (database at ${json.schema.current}, expected ${json.schema.expected})` : "")
      : "no readable body";
    throw new PhaseFailure("readiness", `HTTP ${status}, not ready: ${why}`);
  }
  if (json.dependencies?.database?.state !== "ok" || json.schema?.state !== "compatible") {
    throw new PhaseFailure("readiness", "ready, but database/schema is not ok + compatible");
  }
  ok("readiness", `database ok, schema ${json.schema.current}, redis ${json.dependencies?.redis?.state} (non-gating)`);
  return json;
}

async function web() {
  const { status, error } = await get(`${WEB}/`);
  if (status !== 200) throw new PhaseFailure("web", `GET ${WEB}/ answered ${status || error || "nothing"}`);
  ok("web", WEB);
}

const sha = (value) => createHash("sha256").update(JSON.stringify(value)).digest("hex").slice(0, 16);

/** A read that fails is a failure of the check itself: two identical "could not read" values prove nothing. */
async function read(route) {
  const reply = await get(`${API}/api/v1${route}`);
  if (reply.status !== 200 || reply.json === null) {
    throw new PhaseFailure("unchanged", `could not read GET ${route} (${reply.status || reply.error})`);
  }
  return reply.json;
}

/** Every item of a list (up to the cap), as one digest, plus the total the API reports. */
async function listState(route, cap = 500) {
  const items = [];
  let total = 0;
  while (items.length < cap) {
    const page = await read(`${route}?limit=100&offset=${items.length}`);
    total = page.total;
    items.push(...page.items);
    if (page.items.length === 0 || items.length >= page.total) break;
  }
  return { total, items: items.length, digest: sha(items), capped: items.length < total };
}

/** What the product serves, as bounded deterministic fingerprints. See the header for what this proves. */
async function httpState() {
  const events = await listState("/earnings-events");
  const filings = await listState("/filings");
  const benchmarks = await listState("/benchmarks");
  const records = await listState("/research/records");
  const eps = [];
  for (const filing of (await read("/filings?limit=5")).items) {
    const reply = await get(`${API}/api/v1/filings/${filing.accession}/eps?cik=${filing.company_cik}&period_class=QUARTER&eps_kind=DILUTED`);
    eps.push({ accession: filing.accession, status: reply.status, digest: sha(reply.json) });
  }
  const firstEvent = (await read("/earnings-events?limit=1")).items[0];
  const firstBenchmark = (await read("/benchmarks?limit=1")).items[0];
  let observation = null;
  if (firstEvent) {
    const query = firstBenchmark ? `?benchmark_id=${firstBenchmark.id}&horizons=1,2,5` : "";
    const reply = await get(`${API}/api/v1/research/observations/${firstEvent.id}${query}`);
    observation = { status: reply.status, digest: sha(reply.json) };
  }
  return { events, filings, benchmarks, records, eps, observation };
}

/** The database-side fingerprint (opt-in: it needs the application's Python and database access). */
function dbState() {
  // A shell command line (the operator's own environment; paths with spaces must be quoted by them).
  const command = process.env.QF_SMOKE_FINGERPRINT_COMMAND ?? "uv run --project apps/api python scripts/validation/state_fingerprint.py";
  const flag = process.env.QF_SMOKE_FINGERPRINT_VALIDATION === "1" ? " --validation" : "";
  const run = spawnSync(`${command}${flag}`, { cwd: ROOT, encoding: "utf8", timeout: 120_000, shell: true });
  let parsed = null;
  try {
    parsed = JSON.parse(run.stdout);
  } catch {
    /* reported below */
  }
  if (run.status !== 0 || parsed === null) {
    const why = (run.stderr ?? "").trim().split("\n").slice(-1)[0] || "no output";
    throw new PhaseFailure("unchanged", `--db-fingerprint could not read the database state: ${why}`);
  }
  return parsed;
}

/** Paths at which two snapshots differ, so a failure says WHAT changed. */
function differences(before, after, prefix = "") {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  if (typeof before !== "object" || before === null || typeof after !== "object" || after === null) return [prefix || "(root)"];
  return [...new Set([...Object.keys(before), ...Object.keys(after)])].flatMap((key) => differences(before[key], after[key], prefix ? `${prefix}.${key}` : key));
}

function browser() {
  // QF_SMOKE_BROWSER_SCRIPT exists so the orchestration can be tested without a browser.
  const script = process.env.QF_SMOKE_BROWSER_SCRIPT ?? path.join(ROOT, "scripts", "validation", "real_smoke.mjs");
  const run = spawnSync(process.execPath, [script], {
    env: { ...process.env, QF_WEB_URL: WEB, QF_API_URL: API, REAL_SMOKE_VERIFY: args.has("--verify") ? "1" : "0" },
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    timeout: Number(process.env.QF_SMOKE_BROWSER_TIMEOUT_MS ?? 10 * 60_000), // a hung Chrome must not block forever
  });
  if (run.error?.code === "ETIMEDOUT") throw new PhaseFailure("browser", "the browser phase timed out");
  const lines = `${run.stdout ?? ""}`.split("\n");
  const failed = lines.filter((line) => line.startsWith("SMOKE FAIL "));
  if (run.status !== 0 || failed.length > 0) {
    const detail = failed.length ? failed.map((line) => line.slice("SMOKE FAIL ".length)).slice(0, 5).join("; ") : (run.stderr ?? "").trim().split("\n").slice(-2).join(" ");
    throw new PhaseFailure("browser", `${failed.length || 1} failure(s): ${detail}`);
  }
  ok("browser", lines.find((line) => line.startsWith("SMOKE PASS "))?.slice("SMOKE PASS ".length));
  const requests = lines.find((line) => line.startsWith("SMOKE REQUESTS "))?.slice("SMOKE REQUESTS ".length);
  return requests ? JSON.parse(requests) : null;
}

function build() {
  // NEXT_PUBLIC_* is inlined at BUILD time: the build must point at the API this run is checking.
  const result = spawnSync("npm", ["run", "build"], {
    cwd: WEB_DIR,
    stdio: "ignore",
    env: { ...process.env, NEXT_PUBLIC_API_BASE_URL: API },
  });
  if (result.status !== 0) throw new PhaseFailure("build", "`npm run build` failed in apps/web");
  ok("build", "apps/web production build");
}

async function startWeb() {
  const port = new URL(WEB).port || "3101";
  // Something already answering on the port would satisfy the poll below and a STALE server would be
  // smoked instead of this build.
  if ((await get(`${WEB}/`, 1)).status !== 0) {
    throw new PhaseFailure("web", `--start-web: ${WEB} is already serving; stop it or drop --start-web`);
  }
  const child = spawn("npx", ["next", "start", "-p", port], {
    cwd: WEB_DIR,
    stdio: "ignore",
    detached: true, // its own process group, so the whole npx -> next tree can be stopped
    env: { ...process.env, NEXT_PUBLIC_API_BASE_URL: API },
  });
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if ((await get(`${WEB}/`)).status === 200) return child;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  stop(child);
  throw new PhaseFailure("web", `\`next start\` did not answer on ${WEB} (is there a production build? run with --build)`);
}

/** Stop the server AND its children (npx starts `next` as a grandchild). */
function stop(child) {
  if (!child?.pid) return;
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    child.kill();
  }
}

async function unchanged() {
  const withDb = args.has("--db-fingerprint");
  const before = { http: await httpState(), db: withDb ? dbState() : null };
  const browserRequests = browser();
  if (withDb) await new Promise((resolve) => setTimeout(resolve, 1500)); // PostgreSQL statistics are flushed with a short delay
  const after = { http: await httpState(), db: withDb ? dbState() : null };
  const changed = differences(before, after);
  if (changed.length > 0) {
    const more = changed.length > 6 ? ` (+${changed.length - 6} more)` : "";
    throw new PhaseFailure("unchanged", `stored state changed during the run at: ${changed.slice(0, 6).join(", ")}${more}`);
  }
  await readiness();
  const totals = ["events", "filings", "benchmarks", "records"].map((name) => `${name} ${after.http[name].total}`).join(", ");
  const scope = withDb ? `${Object.keys(after.db).length} tables fingerprinted` : "database fingerprint not requested (HTTP level only)";
  ok("unchanged", `${totals}; ${scope}`);
  ok("requests", `smoke script ${AUDIT.api + AUDIT.web} GET, browser ${JSON.stringify(browserRequests ?? "not reported")}, no write method`);
}

async function main() {
  let server = null;
  try {
    if (args.has("--build")) build();
    await liveness();
    await readiness();
    if (args.has("--start-web")) server = await startWeb();
    await web();
    if (args.has("--no-browser")) {
      console.log("RESULT: PASS (api + web reachability only; browser phase skipped)");
      return 0;
    }
    await unchanged();
    console.log("RESULT: PASS");
    return 0;
  } catch (error) {
    if (!(error instanceof PhaseFailure)) throw error;
    console.log(`FAIL ${error.phase}: ${error.message}`);
    console.log(`RESULT: FAIL (${error.phase})`);
    return 1;
  } finally {
    stop(server);
  }
}

process.exitCode = await main();
