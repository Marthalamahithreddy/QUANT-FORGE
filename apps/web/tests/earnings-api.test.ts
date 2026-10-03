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

describe("buildEarningsPath", () => {
  it("encodes filters and pagination", () => {
    expect(buildEarningsPath({ ticker: "A B", dateFrom: "2024-01-01", offset: 50 })).toBe(
      "/api/v1/earnings-events?ticker=A+B&date_from=2024-01-01&limit=50&offset=50",
    );
  });
});

describe("getEarningsPage", () => {
  it("returns validated data", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ items: [makeEvent()], total: 1, limit: 50, offset: 0 }),
    );
    const result = await getEarningsPage();
    expect(result.state).toBe("ok");
    expect(fetchMock.mock.calls[0]![0]).toContain("/api/v1/earnings-events?");
  });

  it("returns an empty page as ok, not as an error", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }));
    const result = await getEarningsPage();
    expect(result).toMatchObject({ state: "ok", data: { total: 0 } });
  });

  it("reports a transport failure as an error state", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    expect((await getEarningsPage()).state).toBe("error");
  });

  it("reports an HTTP 503 as an error state", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Database unavailable." }, 503));
    const result = await getEarningsPage();
    expect(result.state).toBe("error");
  });

  it("keeps a list-endpoint 422 as an error, not as not_found", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "date_from must not be after date_to." }, 422));
    expect((await getEarningsPage()).state).toBe("error");
  });

  it("rejects a payload that drifts from the contract", async () => {
    const broken = { ...makeEvent(), reported_eps: 1.1 };
    fetchMock.mockResolvedValue(jsonResponse({ items: [broken], total: 1, limit: 50, offset: 0 }));
    expect((await getEarningsPage()).state).toBe("error");
  });

  it("rejects an unknown availability basis rather than guessing its meaning", async () => {
    const event = makeEvent();
    const odd = {
      ...event,
      availability: { ...event.availability, observed_at_basis: "COMPANY_PRESS_RELEASE" },
    };
    fetchMock.mockResolvedValue(jsonResponse({ items: [odd], total: 1, limit: 50, offset: 0 }));
    expect((await getEarningsPage()).state).toBe("error");
  });
});

describe("getEarningsEvent", () => {
  it("maps 404 to not_found", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Event not found." }, 404));
    expect((await getEarningsEvent("x")).state).toBe("not_found");
  });

  it("maps a malformed-id 422 to not_found, not to a backend error", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: [] }, 422));
    expect((await getEarningsEvent("not-a-uuid")).state).toBe("not_found");
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
    const result = await getReadiness();
    expect(result).toMatchObject({
      state: "ok",
      data: { status: "not_ready", dependencies: { database: "unavailable" } },
    });
  });
});
