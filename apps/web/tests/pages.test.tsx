import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EarningsDetailPage from "@/app/earnings/[id]/page";
import EarningsPage from "@/app/earnings/page";
import Overview from "@/app/page";
import ResearchPage from "@/app/research/page";
import { jsonResponse, makeEvent } from "./fixtures";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));

const HEALTH = { status: "ok", service: "quantforge-api", version: "0.1.0", environment: "local" };
const READY = {
  status: "ready",
  service: "quantforge-api",
  dependencies: { database: "ok", redis: "ok" },
};

interface Routes {
  health?: Response | Error;
  ready?: Response | Error;
  earnings?: Response | Error;
}

function route(routes: Routes) {
  const respond = (value: Response | Error | undefined): Promise<Response> => {
    if (value === undefined) return Promise.resolve(jsonResponse({}, 500));
    return value instanceof Error ? Promise.reject(value) : Promise.resolve(value.clone());
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      if (input.includes("/health/ready")) return respond(routes.ready);
      if (input.includes("/earnings-events")) return respond(routes.earnings);
      return respond(routes.health);
    }),
  );
}

const page = (items: unknown[], total = items.length) =>
  jsonResponse({ items, total, limit: 50, offset: 0 });

const listProps = (search: Record<string, string> = {}) =>
  ({ searchParams: Promise.resolve(search), params: Promise.resolve({}) }) as never;

beforeEach(() => route({ health: jsonResponse(HEALTH), ready: jsonResponse(READY) }));
afterEach(() => vi.unstubAllGlobals());

describe("Earnings page", () => {
  it("explains an empty dataset without implying a bug", async () => {
    route({ health: jsonResponse(HEALTH), earnings: page([]) });
    render(await EarningsPage(listProps()));

    expect(screen.getByText("No earnings events are available yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows a system error when the backend is down", async () => {
    route({ health: new TypeError("down"), earnings: new TypeError("fetch failed") });
    render(await EarningsPage(listProps()));

    expect(screen.getByRole("alert")).toHaveTextContent("Earnings data is unavailable");
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
  });

  it("shows an error when the API answers 503", async () => {
    route({ health: jsonResponse(HEALTH), earnings: jsonResponse({ detail: "x" }, 503) });
    render(await EarningsPage(listProps()));

    expect(screen.getByRole("alert")).toHaveTextContent("Earnings data is unavailable");
  });

  it("renders populated events in a table", async () => {
    route({ health: jsonResponse(HEALTH), earnings: page([makeEvent()]) });
    render(await EarningsPage(listProps()));

    const table = screen.getByRole("table");
    expect(within(table).getByText("Acme Corp")).toBeInTheDocument();
    expect(within(table).getByText("ACME")).toBeInTheDocument();
    expect(screen.getByText(/1 stored observation/)).toBeInTheDocument();
  });

  it("states how many events have no evidence rather than hiding them", async () => {
    route({ health: jsonResponse(HEALTH), earnings: page([makeEvent()]) });
    render(await EarningsPage(listProps()));

    expect(screen.getByText(/these are labelled, not hidden/)).toBeInTheDocument();
    expect(screen.getByText(/a revision or a second source/)).toBeInTheDocument();
  });

  it("forwards filters to the API and tells a filtered empty result apart", async () => {
    route({ health: jsonResponse(HEALTH), earnings: page([]) });
    render(await EarningsPage(listProps({ ticker: "zzz", date_from: "2024-01-01" })));

    const fetchMock = vi.mocked(fetch);
    const earningsCall = fetchMock.mock.calls.map((c) => String(c[0])).find((u) => u.includes("earnings-events"));
    expect(earningsCall).toContain("ticker=zzz");
    expect(earningsCall).toContain("date_from=2024-01-01");
    expect(screen.getByText("No events match these filters")).toBeInTheDocument();
  });

  it("rejects an invalid date without calling the API", async () => {
    route({ health: jsonResponse(HEALTH) });
    render(await EarningsPage(listProps({ date_from: "yesterday" })));

    expect(screen.getByText("Invalid filter")).toBeInTheDocument();
    const urls = vi.mocked(fetch).mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("earnings-events"))).toBe(false);
  });
});

describe("Earnings detail page", () => {
  const detailProps = { params: Promise.resolve({ id: "abc" }) } as never;

  it("says availability was not disclosed when observed_at is missing", async () => {
    route({ health: jsonResponse(HEALTH), earnings: jsonResponse(makeEvent()) });
    render(await EarningsDetailPage(detailProps));

    expect(screen.getByText("Availability not disclosed")).toBeInTheDocument();
    expect(screen.getByText(/did not supply a historical availability time/)).toBeInTheDocument();
    expect(screen.getAllByText("Not PIT eligible").length).toBeGreaterThan(0);
  });

  it("describes SEC filing acceptance as filing acceptance only", async () => {
    const event = makeEvent({
      availability: {
        observed_at: "2024-03-15T21:30:15Z",
        observed_at_basis: "SEC_FILING_ACCEPTANCE",
        pit_eligibility: "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS",
        pit_reason: "basis not approved for point-in-time use",
      },
    });
    route({ health: jsonResponse(HEALTH), earnings: jsonResponse(event) });
    render(await EarningsDetailPage(detailProps));

    expect(screen.getByText("SEC filing acceptance")).toBeInTheDocument();
    expect(screen.getByText(/not the earnings release time and does not prove first public disclosure/)).toBeInTheDocument();
    expect(screen.getByText(/not currently approved for point-in-time use/)).toBeInTheDocument();
    expect(screen.getByText("2024-03-15 21:30:15 UTC")).toBeInTheDocument();
    expect(screen.getByText("basis not approved for point-in-time use")).toBeInTheDocument();
  });

  it("describes source publication and shows the eligible state", async () => {
    const event = makeEvent({
      availability: {
        observed_at: "2024-03-15T11:00:00Z",
        observed_at_basis: "SOURCE_PUBLICATION",
        pit_eligibility: "ELIGIBLE",
        pit_reason: "Approved availability evidence supports a point-in-time claim.",
      },
    });
    route({ health: jsonResponse(HEALTH), earnings: jsonResponse(event) });
    render(await EarningsDetailPage(detailProps));

    expect(screen.getByText("Source publication")).toBeInTheDocument();
    expect(screen.getAllByText("PIT eligible").length).toBeGreaterThan(0);
  });

  it("shows EPS and the backend-computed surprise without recomputing", async () => {
    route({ health: jsonResponse(HEALTH), earnings: jsonResponse(makeEvent()) });
    render(await EarningsDetailPage(detailProps));

    expect(screen.getByText("1.1000")).toBeInTheDocument();
    expect(screen.getByText("+0.1000")).toBeInTheDocument();
  });

  it("does not expose provenance internals", async () => {
    route({ health: jsonResponse(HEALTH), earnings: jsonResponse(makeEvent()) });
    render(await EarningsDetailPage(detailProps));

    expect(document.body.textContent).not.toMatch(/fingerprint|ingestion_run|raw|\/data\//i);
  });

  it("calls notFound for an unknown event", async () => {
    route({ health: jsonResponse(HEALTH), earnings: jsonResponse({ detail: "no" }, 404) });
    await expect(EarningsDetailPage(detailProps)).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("shows an error state when the backend fails", async () => {
    route({ health: jsonResponse(HEALTH), earnings: new TypeError("down") });
    render(await EarningsDetailPage(detailProps));

    expect(screen.getByRole("alert")).toHaveTextContent("This event could not be loaded");
  });
});

describe("Overview", () => {
  it("shows real readiness and earnings coverage", async () => {
    route({
      health: jsonResponse(HEALTH),
      ready: jsonResponse(READY),
      earnings: page([makeEvent({ event_date: "2024-05-01" })], 7),
    });
    render(await Overview());

    const system = screen.getByRole("region", { name: "System" });
    expect(within(system).getAllByText("Reachable")).toHaveLength(2);
    const data = screen.getByRole("region", { name: "Earnings data" });
    expect(within(data).getByText("7")).toBeInTheDocument();
    expect(within(data).getByText("2024-05-01")).toBeInTheDocument();
  });

  it("shows a down dependency from a 503 readiness body", async () => {
    route({
      health: jsonResponse(HEALTH),
      ready: jsonResponse(
        { ...READY, status: "not_ready", dependencies: { database: "unavailable", redis: "ok" } },
        503,
      ),
      earnings: page([]),
    });
    render(await Overview());

    const system = screen.getByRole("region", { name: "System" });
    expect(within(system).getAllByText("Unavailable").length).toBeGreaterThan(0);
  });

  it("invents no coverage when the backend is down", async () => {
    route({
      health: new TypeError("down"),
      ready: new TypeError("down"),
      earnings: new TypeError("down"),
    });
    render(await Overview());

    expect(screen.getByText(/Earnings coverage is unavailable/)).toBeInTheDocument();
    expect(screen.queryByText("Earnings observations stored")).toBeNull();
  });

  it("explains an empty store", async () => {
    route({ health: jsonResponse(HEALTH), ready: jsonResponse(READY), earnings: page([]) });
    render(await Overview());

    expect(screen.getByText(/No earnings events are stored yet/)).toBeInTheDocument();
  });
});

describe("Research page", () => {
  it("shows no results and no fabricated metrics", async () => {
    route({ health: jsonResponse(HEALTH) });
    render(await ResearchPage());

    expect(
      screen.getByText("The event-study workspace is not part of the interface yet"),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/sharpe|p&l|equity curve|\d+(\.\d+)?%/i);
  });
});
