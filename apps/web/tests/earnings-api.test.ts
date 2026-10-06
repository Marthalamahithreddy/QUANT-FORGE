import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildEarningsPath,
  getEarningsEvent,
  getEarningsPage,
  getReadiness,
} from "@/lib/api/earnings";
import { jsonResponse, makeEvent } from "./fixtures";

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const page = (items: unknown[], overrides: Record<string, unknown> = {}) => ({
  items,
  total: items.length,
  limit: 50,
  offset: 0,
  ...overrides,
});

describe("buildEarningsPath", () => {
  it("encodes filters and pagination with the backend's parameter names", () => {
    expect(buildEarningsPath({ ticker: "A B", dateFrom: "2024-01-01", offset: 50 })).toBe(
      "/api/v1/earnings-events?ticker=A+B&date_from=2024-01-01&limit=50&offset=50",
    );
  });

  it("sends only the filters that were given", () => {
    expect(buildEarningsPath({})).toBe("/api/v1/earnings-events?limit=50&offset=0");
  });
});

describe("getEarningsPage", () => {
  it("returns validated data from exactly one request", async () => {
    fetchMock.mockResolvedValue(jsonResponse(page([makeEvent()])));
    const result = await getEarningsPage();
    expect(result.kind).toBe("ok");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]![0]).toContain("/api/v1/earnings-events?");
  });

  it("returns an empty page as ok, not as an error", async () => {
    fetchMock.mockResolvedValue(jsonResponse(page([])));
    expect(await getEarningsPage()).toMatchObject({ kind: "ok", data: { total: 0 } });
  });

  it("reports a transport failure as unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    expect((await getEarningsPage()).kind).toBe("unreachable");
  });

  it("reports an HTTP 503 as unavailable, never as an empty page", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Database unavailable." }, 503));
    expect((await getEarningsPage()).kind).toBe("unavailable");
  });

  it("reports a list-endpoint 404 as a service fault, not as 'nothing stored'", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Not Found" }, 404));
    expect(await getEarningsPage()).toEqual({ kind: "server_error", status: 404 });
  });

  it("reports a list-endpoint 422 as rejected, not as not_found", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: [{ loc: ["query", "date_from"], msg: "bad", type: "x" }] }, 422));
    expect((await getEarningsPage()).kind).toBe("rejected");
  });

  it("rejects a payload that drifts from the contract", async () => {
    fetchMock.mockResolvedValue(jsonResponse(page([{ ...makeEvent(), reported_eps: 1.1 }])));
    expect(await getEarningsPage()).toMatchObject({ kind: "contract", path: "$.items[0].reported_eps" });
  });

  it("rejects an unknown availability basis rather than guessing its meaning", async () => {
    const event = makeEvent();
    const odd = { ...event, availability: { ...event.availability, observed_at_basis: "COMPANY_PRESS_RELEASE" } };
    fetchMock.mockResolvedValue(jsonResponse(page([odd])));
    expect((await getEarningsPage()).kind).toBe("contract");
  });

  it("rejects a page that does not echo the requested limit and offset", async () => {
    fetchMock.mockResolvedValue(jsonResponse(page([makeEvent()], { offset: 100 })));
    expect(await getEarningsPage({ offset: 0 })).toMatchObject({ kind: "contract", path: "$.offset" });
    fetchMock.mockResolvedValue(jsonResponse(page([makeEvent()], { limit: 10 })));
    expect(await getEarningsPage()).toMatchObject({ kind: "contract", path: "$.limit" });
  });
});

describe("getEarningsEvent", () => {
  const ID = "11111111-1111-4111-8111-111111111111";

  it("maps 404 to not_found", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Event not found." }, 404));
    expect((await getEarningsEvent(ID)).kind).toBe("not_found");
  });

  it("makes no request for an identifier that is not a UUID", async () => {
    expect((await getEarningsEvent("not-a-uuid")).kind).toBe("not_found");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps a 422 for a well-formed id as rejected, not as not_found", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: [] }, 422));
    expect((await getEarningsEvent(ID)).kind).toBe("rejected");
  });

  it("rejects a response for a different event than the one requested", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makeEvent({ id: "99999999-9999-4999-8999-999999999999" })));
    expect(await getEarningsEvent(ID)).toMatchObject({ kind: "contract", path: "$.id" });
  });

  it("accepts the same UUID in a different letter case", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makeEvent()));
    expect((await getEarningsEvent(ID.toUpperCase())).kind).toBe("ok");
  });
});

describe("getReadiness", () => {
  it("accepts the documented 503 body when a dependency is down", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          status: "not_ready",
          service: "quantforge-api",
          dependencies: { database: "unavailable", redis: "ok" },
          detail: { database: "OperationalError" },
        },
        503,
      ),
    );
    expect(await getReadiness()).toMatchObject({
      kind: "ok",
      data: { status: "not_ready", dependencies: { database: "unavailable" } },
    });
  });

  it("accepts a readiness body without the optional detail", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ status: "ready", service: "quantforge-api", dependencies: { database: "ok", redis: "ok" } }),
    );
    expect((await getReadiness()).kind).toBe("ok");
  });
});
