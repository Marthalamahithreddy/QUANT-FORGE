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
  total?: () => number;
}
const api = (config: Api = {}) =>
  stub((request, response) => {
    const url = new URL(request.url ?? "/", "http://x");
    if (url.pathname === "/health") return json(response, config.liveStatus ?? 200, config.live ?? LIVE);
    if (url.pathname === "/api/v1/health/ready") return json(response, config.readyStatus ?? 200, config.ready ?? READY);
    return json(response, 200, { items: [], total: config.total?.() ?? 3, limit: 1, offset: 0 });
  });
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

  it("stored state changing during the run", async () => {
    let calls = 0;
    const [a, w] = [await api({ total: () => (calls++ < 4 ? 3 : 4) }), await web()];
    const result = await run([], target(a.url, w.url, { QF_SMOKE_BROWSER_SCRIPT: PASSING }));
    expect(result.code).toBe(1);
    expect(result.out).toContain("RESULT: FAIL (unchanged)");
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
    expect(smoke).not.toMatch(/\.fill\(|\.click\(|\.type\(|method:\s*"(POST|PUT|PATCH|DELETE)"|request\.post/);
    expect(smoke).toMatch(/process\.env\.REAL_SMOKE_VERIFY === "1"/);
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
