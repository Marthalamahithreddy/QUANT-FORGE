#!/usr/bin/env node
// Release smoke: "is this deployment serving the product correctly?" -- read-only, no providers.
//
//   node scripts/release-smoke.mjs [--verify] [--build] [--start-web] [--no-browser]
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
//   4 browser    scripts/validation/real_smoke.mjs (every page 200, no 5xx, zero axe violations,
//                no horizontal overflow at 1440 and 390 px)
//   5 unchanged  the stored counts and readiness are the same after the run as before it
//
// What it never does: save a research record, ingest or call a provider, touch accounting data or run
// a migration. Every request this script makes is a GET. The explicit-verify page asks the backend to
// re-verify a saved record (itself read-only) and is only visited with --verify.
//
// Flags: --build runs `npm run build` in apps/web first; --start-web serves that build with
// `next start` on the QF_WEB_URL port for the duration of the run (otherwise a server must already be
// running); --no-browser stops after phase 3 (the API/web checks only); --verify adds the verify page.
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const WEB_DIR = path.join(ROOT, "apps", "web");
const API = (process.env.QF_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const WEB = (process.env.QF_WEB_URL ?? "http://127.0.0.1:3101").replace(/\/+$/, "");
const args = new Set(process.argv.slice(2));
const TIMEOUT_MS = 15_000;

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

/** Counts the product shows. Reading them twice proves the run changed nothing it could change. */
async function counts() {
  const read = async (route) => (await get(`${API}/api/v1${route}`)).json?.total;
  return {
    events: await read("/earnings-events?limit=1"),
    filings: await read("/filings?limit=1"),
    records: await read("/research/records?limit=1"),
    benchmarks: await read("/benchmarks?limit=1"),
  };
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
    const before = await counts();
    browser();
    const after = await counts();
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      throw new PhaseFailure("unchanged", `stored counts changed during the run: ${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
    }
    await readiness();
    ok("unchanged", `counts ${JSON.stringify(after)}`);
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
