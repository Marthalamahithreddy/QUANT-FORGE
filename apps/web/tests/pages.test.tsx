import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EarningsDetailPage from "@/app/earnings/[id]/page";
import EarningsPage from "@/app/earnings/page";
import { jsonResponse, makeEvent } from "./fixtures";
import { renderServer } from "./render-server";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
  redirect: (to: string) => {
    throw new Error(`NEXT_REDIRECT:${to}`);
  },
}));

const HEALTH = { status: "ok", service: "quantforge-api", version: "0.1.0", environment: "local" };
const READY = {
  status: "ready",
  service: "quantforge-api",
  dependencies: { database: { state: "ok", gating: true }, redis: { state: "ok", gating: false } },
  schema: { state: "compatible", expected: "0014", current: "0014", gating: true },
};
const ID = "11111111-1111-4111-8111-111111111111";

interface Routes {
  health?: Response | Error;
  ready?: Response | Error;
  earnings?: Response | Error;
  /** The market-reaction preview's observation read. */
  observation?: Response | Error;
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
      if (input.includes("/research/observations")) return respond(routes.observation);
      if (input.includes("/earnings-events")) return respond(routes.earnings);
      return respond(routes.health);
    }),
  );
}

const apiCalls = (needle: string) =>
  vi.mocked(fetch).mock.calls.map((c) => String(c[0])).filter((url) => url.includes(needle));

const page = (items: unknown[], total = items.length, offset = 0) =>
  jsonResponse({ items, total, limit: 50, offset });

const listProps = (search: Record<string, string | string[]> = {}) =>
  ({ searchParams: Promise.resolve(search), params: Promise.resolve({}) }) as never;
const detailProps = (id = ID) => ({ params: Promise.resolve({ id }) }) as never;

beforeEach(() => route({ health: jsonResponse(HEALTH), ready: jsonResponse(READY) }));
afterEach(() => vi.unstubAllGlobals());

describe("Earnings list page", () => {
  it("makes exactly one API request: the list. The shell owns the health read", async () => {
    route({ earnings: page([makeEvent()]) });
    await renderServer(await EarningsPage(listProps()));

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    expect(apiCalls("/earnings-events")).toHaveLength(1);
    expect(apiCalls("/health")).toHaveLength(0);
  });

  it("explains an empty dataset without implying a bug", async () => {
    route({ earnings: page([]) });
    await renderServer(await EarningsPage(listProps()));

    expect(screen.getByRole("status")).toHaveTextContent("No earnings events are stored");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("tells a transport failure apart from empty data", async () => {
    route({ earnings: new TypeError("fetch failed") });
    await renderServer(await EarningsPage(listProps()));

    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach the QuantForge API");
    expect(screen.queryByText(/No earnings events are stored/)).toBeNull();
  });

  it("shows a temporarily-unavailable state when the API answers 503", async () => {
    route({ earnings: jsonResponse({ detail: "Database unavailable." }, 503) });
    await renderServer(await EarningsPage(listProps()));

    expect(screen.getByRole("alert")).toHaveTextContent("Temporarily unavailable");
  });

  it("shows a contract error, not rows, when the payload drifts", async () => {
    route({ earnings: page([{ ...makeEvent(), reported_eps: 1.5 }]) });
    await renderServer(await EarningsPage(listProps()));

    expect(screen.getByRole("alert")).toHaveTextContent("does not understand");
    expect(screen.queryByRole("table")).toBeNull();
    expect(document.body.textContent).not.toContain("Acme Corp");
  });

  it("renders populated events in a table with the position stated", async () => {
    route({ earnings: page([makeEvent()]) });
    await renderServer(await EarningsPage(listProps()));

    const table = screen.getByRole("table", { name: "Earnings events" });
    expect(within(table).getByText("Acme Corp")).toBeInTheDocument();
    expect(within(table).getByText("ACME")).toBeInTheDocument();
    expect(screen.getByTestId("pagination-position")).toHaveTextContent("Rows 1–1 of 1 stored earnings events");
  });

  it("states that a revision or a second source is its own row", async () => {
    route({ earnings: page([makeEvent()]) });
    await renderServer(await EarningsPage(listProps()));

    expect(screen.getByText(/a revision or a second source/)).toBeInTheDocument();
  });

  it("forwards filters to the API and tells a filtered empty result apart", async () => {
    route({ earnings: page([]) });
    await renderServer(await EarningsPage(listProps({ ticker: "zzz", date_from: "2024-01-01" })));

    const call = apiCalls("earnings-events")[0]!;
    expect(call).toContain("ticker=zzz");
    expect(call).toContain("date_from=2024-01-01");
    expect(screen.getByRole("status")).toHaveTextContent("Nothing matches these filters");
    expect(screen.getByRole("link", { name: "Clear the filters" })).toHaveAttribute("href", "/earnings");
  });

  it("keeps the filter values in the form fields", async () => {
    route({ earnings: page([makeEvent()]) });
    await renderServer(await EarningsPage(listProps({ ticker: "ACME", date_from: "2024-01-01", date_to: "2024-02-01" })));

    expect(screen.getByLabelText(/Ticker/)).toHaveValue("ACME");
    expect(screen.getByLabelText("From")).toHaveValue("2024-01-01");
    expect(screen.getByLabelText("To")).toHaveValue("2024-02-01");
  });

  it("offers only the filters the endpoint supports", async () => {
    route({ earnings: page([makeEvent()]) });
    await renderServer(await EarningsPage(listProps()));

    const form = screen.getByRole("form", { name: "Filter earnings" });
    expect(within(form).getAllByRole("textbox").length + form.querySelectorAll('input[type="date"]').length).toBe(3);
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", "/earnings");
    expect(screen.queryByLabelText(/PIT status/)).toBeNull();
  });

  it("rejects an invalid date without calling the API and associates the field with the error", async () => {
    await renderServer(await EarningsPage(listProps({ date_from: "yesterday" })));

    expect(screen.getByRole("alert")).toHaveTextContent("These filters cannot be used");
    expect(screen.getByRole("alert")).toHaveTextContent("From date must be a real date");
    expect(apiCalls("earnings-events")).toHaveLength(0);
    const field = screen.getByLabelText("From");
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAttribute("aria-describedby", screen.getByRole("alert").id);
  });

  it("redirects a valid but non-canonical address to the canonical one", async () => {
    await expect(EarningsPage(listProps({ ticker: "", page: "1" }))).rejects.toThrow("NEXT_REDIRECT:/earnings");
    await expect(EarningsPage(listProps({ ticker: "A", extra: "1" }))).rejects.toThrow("NEXT_REDIRECT:/earnings?ticker=A");
    expect(apiCalls("earnings-events")).toHaveLength(0);
  });

  it("pages by offset and keeps the filters in the page links", async () => {
    route({ earnings: page([makeEvent()], 120, 50) });
    await renderServer(await EarningsPage(listProps({ page: "2", ticker: "ACME" })));

    expect(apiCalls("earnings-events")[0]).toContain("offset=50");
    const nav = screen.getByRole("navigation", { name: "Pagination" });
    expect(within(nav).getByRole("link", { name: "Previous page" })).toHaveAttribute("href", "/earnings?ticker=ACME");
    expect(within(nav).getByRole("link", { name: "Next page" })).toHaveAttribute("href", "/earnings?ticker=ACME&page=3");
    expect(within(nav).getByText("Page 2 of 3")).toBeInTheDocument();
  });

  it("treats a page past the end as such, not as empty data", async () => {
    route({ earnings: page([], 3, 500) });
    await renderServer(await EarningsPage(listProps({ page: "11" })));

    expect(screen.getByRole("status")).toHaveTextContent("This page is past the last result");
    expect(screen.getByRole("link", { name: "Go to the first page" })).toHaveAttribute("href", "/earnings");
  });

  it("has no client-side sorting or filtering anywhere on the page", async () => {
    route({ earnings: page([makeEvent()]) });
    await renderServer(await EarningsPage(listProps()));

    expect(screen.queryByRole("button", { name: /Reported EPS|Event date/ })).toBeNull();
    expect(document.querySelectorAll("[aria-sort]")).toHaveLength(0);
    expect(screen.queryByText(/loaded rows/)).toBeNull();
  });
});

describe("Earnings detail page", () => {
  it("makes one request for the event (metadata and page share it) and one for the preview", async () => {
    route({ earnings: jsonResponse(makeEvent()), observation: jsonResponse({}, 500) });
    await renderServer(await EarningsDetailPage(detailProps()));

    expect(apiCalls("/earnings-events/")).toHaveLength(1);
    expect(apiCalls("/research/observations")).toHaveLength(1);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it("says availability was not disclosed when observed_at is missing", async () => {
    route({ earnings: jsonResponse(makeEvent()) });
    await renderServer(await EarningsDetailPage(detailProps()));

    const availability = screen.getByRole("region", { name: "Availability" });
    expect(within(availability).getByText(/did not supply a historical publication time/)).toBeInTheDocument();
    expect(within(availability).getAllByText("Not PIT eligible").length).toBeGreaterThan(0);
  });

  it("keeps the descriptive observation and the point-in-time verdict in separate frames", async () => {
    route({ earnings: jsonResponse(makeEvent()) });
    await renderServer(await EarningsDetailPage(detailProps()));

    const observation = screen.getByRole("region", { name: "Stored event" });
    const availability = screen.getByRole("region", { name: "Availability" });
    expect(observation).not.toContainElement(availability);
    expect(within(observation).queryByText(/PIT/)).toBeNull();
    expect(within(observation).getByText("Descriptive")).toBeInTheDocument();
    expect(availability).toHaveTextContent(/does not change the stored event/);
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
    route({ earnings: jsonResponse(event) });
    await renderServer(await EarningsDetailPage(detailProps()));

    expect(screen.getByText("SEC filing acceptance")).toBeInTheDocument();
    expect(screen.getByText(/not the earnings release time and does not prove first public disclosure/)).toBeInTheDocument();
    expect(screen.getByText(/not currently approved for point-in-time use/)).toBeInTheDocument();
    expect(screen.getByText("2024-03-15 21:30:15 UTC")).toBeInTheDocument();
    expect(screen.getByText("basis not approved for point-in-time use")).toBeInTheDocument();
  });

  it("describes source publication and shows the eligible state in the PIT style", async () => {
    const event = makeEvent({
      availability: {
        observed_at: "2024-03-15T11:00:00Z",
        observed_at_basis: "SOURCE_PUBLICATION",
        pit_eligibility: "ELIGIBLE",
        pit_reason: "Approved availability evidence supports a point-in-time claim.",
      },
    });
    route({ earnings: jsonResponse(event) });
    await renderServer(await EarningsDetailPage(detailProps()));

    expect(screen.getByText("Source publication")).toBeInTheDocument();
    const badges = screen.getAllByText("PIT eligible");
    expect(badges.length).toBeGreaterThan(0);
    expect(badges[0]!.closest("[data-mode]")).toHaveAttribute("data-mode", "pit");
  });

  it("shows EPS and the backend-computed surprise exactly, without recomputing", async () => {
    route({ earnings: jsonResponse(makeEvent({ reported_eps: "1.1000", eps_surprise: { status: "AVAILABLE", absolute: "0.1000", ratio: "0.1", reason: "ok" } })) });
    await renderServer(await EarningsDetailPage(detailProps()));

    expect(screen.getByText("1.1000")).toBeInTheDocument();
    expect(screen.getByText("+0.1000")).toBeInTheDocument();
    expect(screen.getByText("+10.00%")).toBeInTheDocument();
  });

  it("shows revenue with grouped integer digits and the exact fraction, and null revenue as not stated", async () => {
    route({ earnings: jsonResponse(makeEvent({ reported_revenue: "94836000000.0000", estimated_revenue: null })) });
    await renderServer(await EarningsDetailPage(detailProps()));

    expect(screen.getByText("94,836,000,000.0000")).toBeInTheDocument();
    const estimated = screen.getByText("Estimated revenue").nextElementSibling!;
    expect(estimated.querySelector('[data-state="NOT_STATED"]')).not.toBeNull();
  });

  it("renders a refused surprise as a domain state with the backend's reason, never as an error", async () => {
    const event = makeEvent({
      eps_surprise: { status: "NOT_POINT_IN_TIME_ELIGIBLE", absolute: null, ratio: null, reason: "withheld in point-in-time mode" },
    });
    route({ earnings: jsonResponse(event) });
    await renderServer(await EarningsDetailPage(detailProps()));

    const row = screen.getByText("EPS surprise").nextElementSibling!;
    expect(row.querySelector('[data-state="REFUSED"]')).not.toBeNull();
    expect(row).toHaveTextContent("withheld in point-in-time mode");
    // (The preview's own failed read, with no observation mocked, is a separate alert elsewhere.)
    expect(within(screen.getByRole("region", { name: "Stored event" })).queryByRole("alert")).toBeNull();
  });

  it("offers the primary Open research action with the event identity explicit in the address", async () => {
    route({ earnings: jsonResponse(makeEvent()) });
    await renderServer(await EarningsDetailPage(detailProps()));

    const action = screen.getByRole("link", { name: /Open Research for Acme Corp, 2024-03-15/ });
    expect(action).toHaveAttribute("href", `/research/events/${ID}`);
    expect(action.getAttribute("href")).not.toMatch(/benchmark|filing|horizon/);
  });

  it("links company context from what the response carries, and invents no company page", async () => {
    route({ earnings: jsonResponse(makeEvent()) });
    await renderServer(await EarningsDetailPage(detailProps()));

    expect(screen.getByRole("link", { name: "Earnings for ACME" })).toHaveAttribute("href", "/earnings?ticker=ACME");
    expect(screen.getByRole("link", { name: "Filings for CIK 0000012345" })).toHaveAttribute("href", "/filings?cik=0000012345");
    for (const link of screen.getAllByRole("link")) expect(link.getAttribute("href")).not.toMatch(/\/companies/);
  });

  it("omits the context links when there is no proven ticker or no CIK", async () => {
    route({ earnings: jsonResponse(makeEvent({ resolved_tickers: [], company: { ...makeEvent().company, cik: null } })) });
    await renderServer(await EarningsDetailPage(detailProps()));

    expect(screen.queryByRole("link", { name: /^Earnings for/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /^Filings for CIK/ })).toBeNull();
    expect(screen.getByText(/no ticker is proven for this company/)).toBeInTheDocument();
  });

  it("lists the company's listings as a table, with an empty state when there are none", async () => {
    route({ earnings: jsonResponse(makeEvent()) });
    const { unmount } = await renderServer(await EarningsDetailPage(detailProps()));
    expect(screen.getByRole("table", { name: "Listings of this company" })).toBeInTheDocument();
    unmount();

    route({ earnings: jsonResponse(makeEvent({ listings: [] })) });
    await renderServer(await EarningsDetailPage(detailProps()));
    expect(screen.getByRole("status")).toHaveTextContent("No listings are stored for this company");
  });

  it("labels the listing start date as first observed, never as when the listing became valid", async () => {
    route({ earnings: jsonResponse(makeEvent()) });
    await renderServer(await EarningsDetailPage(detailProps()));
    const table = screen.getByRole("table", { name: "Listings of this company" });
    expect(within(table).getByRole("columnheader", { name: "First observed" })).toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: "Valid from" })).toBeNull();
    expect(screen.getByText(/first observed this listing evidence/)).toHaveTextContent(
      /does not say when the listing, the ticker or the exchange relationship began/,
    );
  });

  it("exposes breadcrumbs and one h1 with sections as h2", async () => {
    route({ earnings: jsonResponse(makeEvent()) });
    await renderServer(await EarningsDetailPage(detailProps()));

    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Earnings" })).toHaveAttribute("href", "/earnings");
    expect(within(crumbs).getByText("Acme Corp · 2024-03-15")).toHaveAttribute("aria-current", "page");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const h2 = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(h2).toEqual(expect.arrayContaining(["Stored event", "Availability", "Provenance"]));
  });

  it("does not expose provenance internals", async () => {
    route({ earnings: jsonResponse(makeEvent()) });
    await renderServer(await EarningsDetailPage(detailProps()));

    expect(document.body.textContent).not.toMatch(/fingerprint|ingestion_run|raw|\/data\//i);
  });

  it("a failed preview leaves the rest of the record intact and says so safely", async () => {
    route({ earnings: jsonResponse(makeEvent()), observation: new TypeError("connect ECONNREFUSED 127.0.0.1:8000") });
    await renderServer(await EarningsDetailPage(detailProps()));

    const preview = screen.getByRole("region", { name: "Market reaction" });
    expect(within(preview).getByRole("alert")).toHaveTextContent("Cannot reach the QuantForge API");
    expect(preview.textContent).not.toMatch(/127\.0\.0\.1|8000/);
    expect(screen.getByRole("region", { name: "Stored event" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Availability" })).toBeInTheDocument();
  });

  it("calls notFound for an unknown event and for an identifier that is not a UUID (no request)", async () => {
    route({ earnings: jsonResponse({ detail: "no" }, 404) });
    await expect(EarningsDetailPage(detailProps())).rejects.toThrow("NEXT_NOT_FOUND");
    vi.mocked(fetch).mockClear();
    await expect(EarningsDetailPage(detailProps("not-a-uuid"))).rejects.toThrow("NEXT_NOT_FOUND");
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("shows a safe error state when the backend fails", async () => {
    route({ earnings: new TypeError("connect ECONNREFUSED 127.0.0.1:8000") });
    await renderServer(await EarningsDetailPage(detailProps("22222222-2222-4222-8222-222222222222")));

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Cannot reach the QuantForge API");
    expect(alert.textContent).not.toMatch(/127\.0\.0\.1|8000|ECONNREFUSED/);
  });

  it("shows a contract error and none of the payload when the event drifts", async () => {
    route({ earnings: jsonResponse({ ...makeEvent(), reported_eps: 1.1 }) });
    await renderServer(await EarningsDetailPage(detailProps()));

    expect(screen.getByRole("alert")).toHaveTextContent("does not understand");
    expect(document.body.textContent).not.toContain("Acme Corp");
  });
});
