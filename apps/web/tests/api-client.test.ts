import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { READ_TIMEOUT_MS, apiRequest, buildUrl } from "@/lib/api/client";
import { object, string } from "@/lib/api/decode";
import { failureCopy } from "@/lib/api/messages";
import type { ApiFailure, ApiResult } from "@/lib/api/result";
import { jsonResponse } from "./fixtures";

const decoder = object<{ name: string }>({ name: string });
const BASE = "http://api.internal:9000";

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const get = (path = "/ping", options = {}) => apiRequest(path, decoder, { baseUrl: BASE, ...options });

describe("buildUrl", () => {
  it.each([
    ["http://localhost:8000", "/api/v1/health", "http://localhost:8000/api/v1/health"],
    ["http://localhost:8000/", "/api/v1/health", "http://localhost:8000/api/v1/health"],
    ["http://localhost:8000///", "/api/v1/health", "http://localhost:8000/api/v1/health"],
    ["http://localhost:8000", "api/v1/health", "http://localhost:8000/api/v1/health"],
  ])("joins %s with %s", (base, path, expected) => {
    expect(buildUrl(base, path)).toBe(expected);
  });
});

describe("apiRequest: request shape", () => {
  it("issues one GET with an Accept header, no caching and a timeout signal", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ name: "x" }));
    await get("/ping", { timeoutMs: 1234 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${BASE}/ping`);
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>).accept).toBe("application/json");
    expect(init.cache).toBe("no-store");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("defaults to the 4 s read timeout class", () => {
    expect(READ_TIMEOUT_MS).toBe(4000);
  });
});

describe("apiRequest: the ApiResult taxonomy", () => {
  it("decodes a success into ok", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ name: "x", extra: 1 }));
    expect(await get()).toEqual({ kind: "ok", data: { name: "x" } });
  });

  it.each<[string, number, ApiResult<unknown>["kind"]]>([
    ["409", 409, "conflict"],
    ["422", 422, "rejected"],
    ["503", 503, "unavailable"],
    ["500", 500, "server_error"],
    ["502", 502, "server_error"],
    ["an undocumented 401", 401, "server_error"],
  ])("maps HTTP %s to %s", async (_label, status, kind) => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "x" }, status));
    expect((await get()).kind).toBe(kind);
  });

  it("treats a 404 as 'nothing stored' only for a read of ONE resource", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Not Found" }, 404));
    expect(await get("/thing/1", { notFoundMeansAbsent: true })).toEqual({ kind: "not_found" });
    // A list, health or readiness 404 means a wrong path or gateway: a service fault, never "no data".
    expect(await get("/things")).toEqual({ kind: "server_error", status: 404 });
  });

  it("carries no response text for a 409", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "duplicate key value violates constraint pk_x" }, 409));
    const result = await get();
    expect(result).toEqual({ kind: "conflict" });
    expect(JSON.stringify([result, failureCopy(result as ApiFailure)])).not.toMatch(/duplicate|constraint|pk_x/);
  });

  it("reports a body that stalls past the timeout as unreachable (timed out), not as a contract failure", async () => {
    fetchMock.mockImplementation((_url: string, init: RequestInit) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "TimeoutError")));
          }),
      } as unknown as Response),
    );
    expect(await get("/slow", { timeoutMs: 20 })).toEqual({ kind: "unreachable", timedOut: true });
  });

  it("reports a refused connection as unreachable (not timed out)", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed: ECONNREFUSED 127.0.0.1:9000"));
    expect(await get()).toEqual({ kind: "unreachable", timedOut: false });
  });

  it("reports a timeout as unreachable with timedOut", async () => {
    fetchMock.mockRejectedValue(new DOMException("timed out", "TimeoutError"));
    expect(await get()).toEqual({ kind: "unreachable", timedOut: true });
  });

  it("reports a body that is not JSON as a contract failure", async () => {
    fetchMock.mockResolvedValue(new Response("<html>nope</html>", { status: 200 }));
    expect(await get()).toMatchObject({ kind: "contract", path: "$" });
  });

  it("reports a payload that breaks the contract with its path, never its content", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ name: 42 }));
    const result = await get();
    expect(result).toEqual({ kind: "contract", path: "$.name", expected: "a string" });
    expect(JSON.stringify(result)).not.toContain("42");
  });

  it("decodes an accepted non-2xx status as a success body (readiness)", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ name: "down" }, 503));
    expect(await get("/ready", { acceptStatuses: [503] })).toEqual({ kind: "ok", data: { name: "down" } });
  });

  it("does not turn a domain refusal inside a 200 into a transport failure", async () => {
    // The body says REFUSED; transport-wise this is a success carrying a domain state.
    const refusal = object<{ status: string }>({ status: string });
    fetchMock.mockResolvedValue(jsonResponse({ status: "REFUSED" }));
    expect(await apiRequest("/x", refusal, { baseUrl: BASE })).toEqual({ kind: "ok", data: { status: "REFUSED" } });
  });
});

describe("apiRequest: 422 issues", () => {
  it("keeps the location and message and drops the echoed input", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { detail: [{ loc: ["query", "ticker"], msg: "String should have at most 32 characters", type: "x", input: "SECRET-INPUT" }] },
        422,
      ),
    );
    const result = await get();
    expect(result).toEqual({
      kind: "rejected",
      issues: [{ where: "query.ticker", message: "String should have at most 32 characters" }],
    });
    expect(JSON.stringify(result)).not.toContain("SECRET-INPUT");
  });

  it("caps and clips what it keeps, and survives an unexpected body", async () => {
    const many = Array.from({ length: 50 }, (_, index) => ({ loc: ["query", `p${index}`], msg: "m".repeat(500), type: "t" }));
    fetchMock.mockResolvedValue(jsonResponse({ detail: many }, 422));
    const result = (await get()) as Extract<ApiResult<unknown>, { kind: "rejected" }>;
    expect(result.issues).toHaveLength(10);
    expect(result.issues[0]?.message.length).toBeLessThanOrEqual(201);

    fetchMock.mockResolvedValue(new Response("not json", { status: 422 }));
    expect(await get()).toEqual({ kind: "rejected", issues: [] });
  });

  it("redacts an address a message might contain", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: [{ loc: ["query"], msg: "bad at http://10.0.0.5:8000/internal", type: "t" }] }, 422),
    );
    const result = await get();
    expect(JSON.stringify(result)).not.toMatch(/10\.0\.0\.5|:8000|internal/);
  });
});

describe("safe failures (no address, host, stack, SQL or raw exception)", () => {
  const FAILURES: readonly [string, () => void][] = [
    ["a refused connection", () => fetchMock.mockRejectedValue(new TypeError("fetch failed: connect ECONNREFUSED 10.9.8.7:5432"))],
    ["a 500 with a stack-like body", () => fetchMock.mockResolvedValue(jsonResponse({ detail: "Traceback at /srv/app/db.py: SELECT * FROM x -- postgresql://u:p@db/qf" }, 500))],
    ["a 503", () => fetchMock.mockResolvedValue(jsonResponse({ detail: "Database unavailable." }, 503))],
    ["a 409 detail with an address", () => fetchMock.mockResolvedValue(jsonResponse({ detail: "see http://10.9.8.7:8000/x" }, 409))],
    ["a 404 on a collection", () => fetchMock.mockResolvedValue(jsonResponse({ detail: "no route /secret/path" }, 404))],
    ["a contract failure", () => fetchMock.mockResolvedValue(jsonResponse({ name: 7 }))],
  ];

  it.each(FAILURES)("never surfaces internals for %s", async (_label, arrange) => {
    arrange();
    const result = await get("/secret/path?q=1", { baseUrl: "http://10.9.8.7:5432" });
    if (result.kind === "ok") throw new Error("expected a failure");
    const copy = failureCopy(result as ApiFailure);
    const shown = JSON.stringify([result, copy]);
    expect(shown).not.toMatch(/10\.9\.8\.7|5432|\/secret|postgresql|SELECT|Traceback|\/srv\/|localhost|https?:/i);
  });
});
