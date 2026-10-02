import { describe, expect, it, vi } from "vitest";

import { apiGet, ApiError, buildUrl } from "@/lib/api/client";

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

describe("apiGet", () => {
  it("sends an Accept header and disables caching", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiGet("/ping", { baseUrl: "http://localhost:8000" });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers.accept).toBe("application/json");
    expect(init.cache).toBe("no-store");
    vi.unstubAllGlobals();
  });

  it("applies a timeout signal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiGet("/ping", { baseUrl: "http://localhost:8000", timeoutMs: 1234 });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.signal).toBeInstanceOf(AbortSignal);
    vi.unstubAllGlobals();
  });

  it("throws an ApiError carrying the URL and status on HTTP failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("", { status: 503, statusText: "Unavailable" })),
    );

    const error = await apiGet("/ping", { baseUrl: "http://x" }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(503);
    expect(apiError.url).toBe("http://x/ping");
    expect(apiError.message).toContain("503");
    vi.unstubAllGlobals();
  });

  it("reports status 0 when no response was produced", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const error = await apiGet("/ping", { baseUrl: "http://x" }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).message).toContain("Cannot reach");
    vi.unstubAllGlobals();
  });
});
