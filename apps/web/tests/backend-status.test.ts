import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getBackendStatus } from "@/lib/api/health";

const HEALTH_PATH = "/api/v1/health";
const HEALTH_PAYLOAD = {
  status: "ok",
  service: "quantforge-api",
  version: "0.1.0",
  environment: "local",
};

const jsonResponse = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getBackendStatus", () => {
  it("requests the versioned health endpoint", async () => {
    fetchMock.mockResolvedValue(jsonResponse(HEALTH_PAYLOAD));

    await getBackendStatus();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`http://localhost:8000${HEALTH_PATH}`);
    expect(init.method).toBe("GET");
    expect(init.cache).toBe("no-store");
  });

  it("returns a connected state carrying the validated payload", async () => {
    fetchMock.mockResolvedValue(jsonResponse(HEALTH_PAYLOAD));

    const status = await getBackendStatus();

    expect(status).toEqual({
      state: "connected",
      health: HEALTH_PAYLOAD,
    });
  });

  it("returns unavailable when the backend refuses the connection", async () => {
    fetchMock.mockRejectedValue(
      new TypeError("fetch failed: ECONNREFUSED"),
    );

    const status = await getBackendStatus();

    expect(status.state).toBe("unavailable");
    if (status.state === "unavailable") {
      // A reader is told the service could not be reached, never WHERE it was looked for.
      expect(status.detail).toContain("could not be reached");
      expect(status.detail).not.toMatch(/https?:|localhost|\/api\//);
    }
  });

  it("never fabricates a connected state on transport failure", async () => {
    fetchMock.mockRejectedValue(new Error("boom"));

    await expect(getBackendStatus()).resolves.toMatchObject({
      state: "unavailable",
    });
  });

  it("returns unavailable on a non-2xx response", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "boom" }, { status: 500, statusText: "Server Error" }),
    );

    const status = await getBackendStatus();

    expect(status.state).toBe("unavailable");
    if (status.state === "unavailable") {
      expect(status.detail).toContain("HTTP 500");
    }
  });

  it("returns unavailable when the body is not JSON", async () => {
    fetchMock.mockResolvedValue(new Response("<html>nope</html>", { status: 200 }));

    const status = await getBackendStatus();

    expect(status.state).toBe("unavailable");
    if (status.state === "unavailable") {
      expect(status.detail).toMatch(/does not understand/);
    }
  });

  it("returns unavailable when the payload violates the contract", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "ok" }));

    const status = await getBackendStatus();

    expect(status.state).toBe("unavailable");
    if (status.state === "unavailable") {
      expect(status.detail).toMatch(/does not understand/);
    }
  });

  it("honours a caller-supplied base URL", async () => {
    fetchMock.mockResolvedValue(jsonResponse(HEALTH_PAYLOAD));

    await getBackendStatus({ baseUrl: "http://api.internal:9000" });

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe(`http://api.internal:9000${HEALTH_PATH}`);
  });

  it("does not reject, so the page can always render", async () => {
    fetchMock.mockRejectedValue(new Error("network down"));

    await expect(getBackendStatus()).resolves.toBeDefined();
  });
});
