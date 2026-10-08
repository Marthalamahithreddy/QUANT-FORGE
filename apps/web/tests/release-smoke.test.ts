import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

/**
 * The release-smoke wrapper's orchestration, driven black-box against tiny HTTP stubs: which phase
 * fails, what it says, the exit code, and that every request it makes is a GET. The real browser phase
 * is `scripts/validation/real_smoke.mjs`; here it is replaced (QF_SMOKE_BROWSER_SCRIPT) by a stub.
 */
const SCRIPT = join(process.cwd(), "..", "..", "scripts", "release-smoke.mjs");

const READY = {
  status: "ready",
  service: "quantforge-api",
  dependencies: { database: { state: "ok", gating: true }, redis: { state: "ok", gating: false } },
  schema: { state: "compatible", expected: "0014", current: "0014", gating: true },
  detail: null,
};
const LIVE = { status: "ok", service: "quantforge-api", version: "0.1.0", environment: "production" };

interface Stub {
  readonly url: string;
  readonly server: Server;
  readonly methods: string[];
}
const open: Server[] = [];
afterEach(() => {
  for (const server of open.splice(0)) server.close();
});

type Handler = (request: IncomingMessage, response: ServerResponse) => void;
async function stub(handler: Handler): Promise<Stub> {
  const methods: string[] = [];
  const server = createServer((request, response) => {
    methods.push(request.method ?? "?");
    handler(request, response);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  open.push(server);
  return { url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`, server, methods };
}

const json = (response: ServerResponse, status: number, body: unknown) => {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
};

interface Api {
  live?: unknown;
  liveStatus?: number;
  ready?: unknown;
  readyStatus?: number;
  /** Routes (path prefix) answered with this status instead of data. */
  failing?: Record<string, number>;
}
/**
 * A stand-in API with stored state a test can change through `GET /__flip` (a stub-only side effect the
 * browser stub triggers, to simulate the product writing during the run): `version` changes an item's
 * content without changing any total.
 */
const state = { version: 0, extra: 0 };
const api = (config: Api = {}) => {
  state.version = 0;
  state.extra = 0;
  return stub((request, response) => {
    const url = new URL(request.url ?? "/", "http://x");
    if (url.pathname === "/health") return json(response, config.liveStatus ?? 200, config.live ?? LIVE);
    if (url.pathname === "/api/v1/health/ready") return json(response, config.readyStatus ?? 200, config.ready ?? READY);
    if (url.pathname === "/__flip") {
      if (url.searchParams.get("kind") === "extra") state.extra += 1;
      else state.version += 1;
      return json(response, 200, { ok: true });
    }
    const failing = Object.entries(config.failing ?? {}).find(([prefix]) => url.pathname.startsWith(`/api/v1${prefix}`));
    if (failing) return json(response, failing[1], { detail: "stub failure" });
    if (url.pathname.includes("/research/observations/")) return json(response, 200, { version: state.version });
    if (url.pathname.endsWith("/eps")) return json(response, 200, { eps: "0.3500" });
    const items = [{ id: "e1", accession: "0000320187-26-000037", company_cik: "0000320187", record_id: "r1", note: state.version }];
    return json(response, 200, { items, total: 1 + state.extra, limit: 100, offset: 0 });
  });
};
const web = (status = 200) =>
  stub((_request, response) => {
    response.writeHead(status, { "content-type": "text/html" });
    response.end("<html></html>");
  });

function browserStub(source: string): string {
  const file = join(mkdtempSync(join(tmpdir(), "qf-smoke-")), "browser.mjs");
  writeFileSync(file, source);
  return file;
}
const PASSING = browserStub('console.log("SMOKE PASS 9 page(s) x 2 viewports");');
const FAILING = browserStub('console.log("SMOKE FAIL desktop saved-record: 2 axe violation(s): color-contrast"); process.exitCode = 1;');

function run(args: string[], env: Record<string, string>): Promise<{ code: number; out: string }> {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [SCRIPT, ...args], { env: { ...process.env, ...env } });
    let out = "";
    child.stdout.on("data", (chunk) => (out += chunk));
    child.stderr.on("data", (chunk) => (out += chunk));
    child.on("close", (code) => resolve({ code: code ?? -1, out }));
  });
}
const target = (apiUrl: string, webUrl: string, extra: Record<string, string> = {}) => ({
  QF_API_URL: apiUrl,
  QF_WEB_URL: webUrl,
  ...extra,
});

describe("release smoke: success", () => {
  it("passes when liveness, readiness and the web server are good", async () => {
    const [a, w] = [await api(), await web()];
    const result = await run(["--no-browser"], target(a.url, w.url));
    expect(result.code).toBe(0);
    expect(result.out).toContain("RESULT: PASS");
    expect(result.out).toMatch(/\[ok\]\s+readiness\s+database ok, schema 0014/);
  });

  it("runs the browser phase and checks that nothing changed", async () => {
    const [a, w] = [await api(), await web()];
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING }));
    expect(result.code).toBe(0);
    expect(result.out).toMatch(/\[ok\]\s+browser\s+9 page\(s\) x 2 viewports/);
    expect(result.out).toMatch(/\[ok\]\s+unchanged/);
    expect(result.out).toContain("RESULT: PASS");
  });

  it("does not fail on Redis being down: it is reported and non-gating", async () => {
    const redisDown = { ...READY, dependencies: { ...READY.dependencies, redis: { state: "unavailable", gating: false } } };
    const [a, w] = [await api({ ready: redisDown }), await web()];
    const result = await run(["--no-browser"], target(a.url, w.url));
    expect(result.code).toBe(0);
    expect(result.out).toContain("redis unavailable (non-gating)");
  });
});

describe("release smoke: each failure names its phase and exits non-zero", () => {
  it("liveness down", async () => {
    const w = await web();
    const result = await run(["--no-browser"], target("http://127.0.0.1:1", w.url));
    expect(result.code).toBe(1);
    expect(result.out).toContain("FAIL liveness");
    expect(result.out).toContain("RESULT: FAIL (liveness)");
  });

  it("readiness 503 because the schema is behind says so, briefly", async () => {
    const behind = {
      ...READY,
      status: "not_ready",
      schema: { state: "mismatch", expected: "0014", current: "0013", gating: true },
    };
    const [a, w] = [await api({ ready: behind, readyStatus: 503 }), await web()];
    const result = await run(["--no-browser"], target(a.url, w.url));
    expect(result.code).toBe(1);
    expect(result.out).toContain("FAIL readiness: HTTP 503, not ready: database ok, schema mismatch (database at 0013, expected 0014)");
  });

  it("readiness 200 that is nonetheless not ok + compatible is refused", async () => {
    const odd = { ...READY, schema: { ...READY.schema, state: "unknown" } };
    const [a, w] = [await api({ ready: odd }), await web()];
    const result = await run(["--no-browser"], target(a.url, w.url));
    expect(result.code).toBe(1);
    expect(result.out).toContain("RESULT: FAIL (readiness)");
  });

  it.each([[500], [404]])("the web server answering %i", async (status) => {
    const [a, w] = [await api(), await web(status)];
    const result = await run(["--no-browser"], target(a.url, w.url));
    expect(result.code).toBe(1);
    expect(result.out).toContain("RESULT: FAIL (web)");
  });

  it("the browser phase reporting violations", async () => {
    const [a, w] = [await api(), await web()];
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: FAILING }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("FAIL browser: 1 failure(s): desktop saved-record: 2 axe violation(s): color-contrast");
  });

  it("a stored item changing its CONTENT with every total unchanged (the old four-total check missed this)", async () => {
    const [a, w] = [await api(), await web()];
    const flip = browserStub('await fetch(process.env.QF_API_URL + "/__flip");');
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: flip }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("RESULT: FAIL (unchanged)");
    expect(result.out).toMatch(/stored state changed during the run at: http\.(events|filings|benchmarks|records|observation)/);
  });

  it("a total changing (a record appearing) during the run", async () => {
    const [a, w] = [await api(), await web()];
    const flip = browserStub('await fetch(process.env.QF_API_URL + "/__flip?kind=extra");');
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: flip }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("stored state changed during the run at: http.events.total");
  });

  it("an unreadable state list fails the check instead of comparing 'nothing' with 'nothing'", async () => {
    const [a, w] = [await api({ failing: { "/filings": 500 } }), await web()];
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("FAIL unchanged: could not read GET /filings");
  });

  it("an unexpected 5xx reported by the browser phase fails the run", async () => {
    const [a, w] = [await api(), await web()];
    const five = browserStub('console.log("SMOKE FAIL desktop research-spy: 502 from /api/v1/research/observations/x"); process.exitCode = 1;');
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: five }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("FAIL browser: 1 failure(s): desktop research-spy: 502");
  });

  it.each([
    ["an application error state served with HTTP 200", "desktop earnings-event: application error state rendered with HTTP 200"],
    ["a list that is empty although the API reports rows", "desktop earnings: expected at least 1 row(s) because the API reports data, found 0"],
    ["horizontal overflow", "mobile filings: horizontal overflow (900 > 390)"],
    ["an accessibility violation", "desktop overview: 1 axe violation(s): image-alt"],
    ["a mutation attempt by the browser", "desktop saved-record: the browser issued POST /api/v1/research/records"],
  ])("the browser phase reporting %s fails the run and says so", async (_name, line) => {
    const [a, w] = [await api(), await web()];
    const failing = browserStub(`console.log(${JSON.stringify(`SMOKE FAIL ${line}`)}); process.exitCode = 1;`);
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: failing }));
    expect(result.code).toBe(1);
    expect(result.out).toContain(`FAIL browser: 1 failure(s): ${line}`);
  });

  it("a browser script that crashes without a verdict fails (no silent pass)", async () => {
    const [a, w] = [await api(), await web()];
    const crash = browserStub('throw new Error("boom");');
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: crash }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("RESULT: FAIL (browser)");
  });

  it("stops at the first failing phase: the browser never runs after a readiness failure", async () => {
    const [a, w] = [await api({ ready: { ...READY, status: "not_ready" }, readyStatus: 503 }), await web()];
    const marker = join(mkdtempSync(join(tmpdir(), "qf-smoke-")), "ran");
    const spy = browserStub(`import { writeFileSync } from "node:fs"; writeFileSync(${JSON.stringify(marker)}, "x");`);
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: spy }));
    expect(result.code).toBe(1);
    expect(() => readFileSync(marker)).toThrow();
  });
});

describe("release smoke: it cannot mutate anything", () => {
  it("issues only GET requests, to the API and the web server", async () => {
    const [a, w] = [await api(), await web()];
    await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING }));
    expect([...new Set([...a.methods, ...w.methods])]).toEqual(["GET"]);
  });

  it("its source contains no write verb, migration, ingestion or save step", () => {
    const source = readFileSync(SCRIPT, "utf8").replace(/\/\/.*$/gm, "");
    for (const forbidden of [/"POST"|"PUT"|"PATCH"|"DELETE"/, /alembic/i, /ingestion/i, /sec-filing|stock-splits/i, /method:\s*"(?!GET")/]) {
      expect(source).not.toMatch(forbidden);
    }
    expect(source).not.toMatch(/records\/?[`"']?\s*,\s*\{\s*method/); // never a POST to the records API
  });

  it("the browser script it calls visits pages only, and the verify page only on request", () => {
    const smoke = readFileSync(join(process.cwd(), "..", "..", "scripts", "validation", "real_smoke.mjs"), "utf8");
    // It never drives the page (no typing, clicking or form submission) and never issues a write request.
    expect(smoke).not.toMatch(/\b(page|locator|frame)\.(fill|click|dblclick|type|press|check|setInputFiles|dispatchEvent)\(|method:\s*"(POST|PUT|PATCH|DELETE)"|request\.post|\.submit\(/);
    expect(smoke).toMatch(/process\.env\.REAL_SMOKE_VERIFY === "1"/);
  });
});

describe("release smoke: the database-side fingerprint (--db-fingerprint)", () => {
  const table = (writes: number[], head = "h1") => ({ earnings_event: { rows: 1, writes, head, tail: head, keys: "k" } });
  /** A fingerprint command that reports a different state on its second call. */
  function fingerprintCommand(first: unknown, second: unknown): string {
    const dir = mkdtempSync(join(tmpdir(), "qf-fp-"));
    const script = join(dir, "fp.mjs");
    const counter = join(dir, "n");
    writeFileSync(
      script,
      `import { existsSync, writeFileSync } from "node:fs";
       const second = existsSync(${JSON.stringify(counter)});
       writeFileSync(${JSON.stringify(counter)}, "x");
       console.log(JSON.stringify(second ? ${JSON.stringify(second)} : ${JSON.stringify(first)}));`,
    );
    return `"${process.execPath}" "${script}"`;
  }

  it("passes when every table's rows, write counters and digests are the same", async () => {
    const [a, w] = [await api(), await web()];
    const same = table([1, 0, 0]);
    const command = fingerprintCommand(same, same);
    const result = await run(["--db-fingerprint"], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING, QF_SMOKE_FINGERPRINT_COMMAND: command }));
    expect(result.code).toBe(0);
    expect(result.out).toMatch(/unchanged\s+.*1 tables fingerprinted/);
  });

  it("fails naming the table when a write counter moved (an UPDATE that changed no row count)", async () => {
    const [a, w] = [await api(), await web()];
    const command = fingerprintCommand(table([1, 0, 0]), table([1, 1, 0]));
    const result = await run(["--db-fingerprint"], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING, QF_SMOKE_FINGERPRINT_COMMAND: command }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("stored state changed during the run at: db.earnings_event.writes");
  });

  it("fails when a row digest changed even though the counters did not", async () => {
    const [a, w] = [await api(), await web()];
    const command = fingerprintCommand(table([1, 0, 0], "h1"), table([1, 0, 0], "h2"));
    const result = await run(["--db-fingerprint"], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING, QF_SMOKE_FINGERPRINT_COMMAND: command }));
    expect(result.code).toBe(1);
    expect(result.out).toMatch(/db\.earnings_event\.(head|tail)/);
  });

  it("a fingerprint command that cannot read the database fails the check (no silent skip)", async () => {
    const [a, w] = [await api(), await web()];
    const broken = browserStub('process.stderr.write("state fingerprint failed: OperationalError\\n"); process.exitCode = 1;');
    const result = await run(["--db-fingerprint"], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING, QF_SMOKE_FINGERPRINT_COMMAND: `"${process.execPath}" "${broken}"` }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("FAIL unchanged: --db-fingerprint could not read the database state: state fingerprint failed: OperationalError");
  });

  it("without the flag it says plainly that the database was not fingerprinted", async () => {
    const [a, w] = [await api(), await web()];
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING }));
    expect(result.out).toContain("database fingerprint not requested (HTTP level only)");
  });
});

describe("release smoke: request audit", () => {
  it("reports how many requests it issued and that none was a write", async () => {
    const [a, w] = [await api(), await web()];
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING }));
    expect(result.out).toMatch(/\[ok\]\s+requests\s+smoke script \d+ GET, browser .*no write method/);
  });

  it("always stops a server it started, whatever the outcome", () => {
    const source = readFileSync(SCRIPT, "utf8");
    expect(source).toMatch(/finally \{\s*stop\(server\);\s*\}/);
    expect(source).toMatch(/detached: true/); // its own process group, so npx -> next is stopped as a tree
  });
});

describe("release smoke: edge cases from review", () => {
  it("--start-web refuses a port something is already serving (a stale server must not be smoked)", async () => {
    const [a, w] = [await api(), await web()];
    const result = await run(["--no-browser", "--start-web"], target(a.url, w.url));
    expect(result.code).toBe(1);
    expect(result.out).toContain("already serving");
    expect(result.out).toContain("RESULT: FAIL (web)");
  });

  it("a hung browser phase fails by timeout instead of blocking forever", async () => {
    const [a, w] = [await api(), await web()];
    const hung = browserStub("setTimeout(() => {}, 60000);");
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: hung, QF_SMOKE_BROWSER_TIMEOUT_MS: "300" }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("FAIL browser: the browser phase timed out");
  });

  it("the build is pointed at the API under test (NEXT_PUBLIC_* is inlined at build time)", () => {
    const source = readFileSync(SCRIPT, "utf8");
    expect(source).toMatch(/spawnSync\("npm", \["run", "build"\][\s\S]*NEXT_PUBLIC_API_BASE_URL: API/);
  });
});
