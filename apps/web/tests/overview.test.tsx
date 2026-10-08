/* eslint-disable @typescript-eslint/no-explicit-any -- captured payloads are deliberately untyped here */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Overview from "@/app/page";
import { jsonResponse, makeEvent } from "./fixtures";
import { RECORDS } from "./records-fixtures";
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

type Reply = Response | Error;
interface Api {
  health?: Reply;
  ready?: Reply;
  earnings?: Reply;
  filings?: Reply;
  records?: Reply;
}

const HEALTH = { status: "ok", service: "quantforge-api", version: "0.1.0", environment: "local" };
const READY = {
  status: "ready",
  service: "quantforge-api",
  dependencies: { database: { state: "ok", gating: true }, redis: { state: "ok", gating: false } },
  schema: { state: "compatible", expected: "0014", current: "0014", gating: true },
};
const readyWith = (patch: Record<string, unknown>) => ({ ...READY, ...patch });
const VERTICAL = JSON.parse(readFileSync(join(process.cwd(), "tests/contract/vertical.json"), "utf8"));
const RECORD_ITEMS = (RECORDS.lists.limit_20!.body as any).items as any[];

const earningsPage = (items: unknown[], total = items.length) => jsonResponse({ items, total, limit: 5, offset: 0 });
const filingsPage = (items: unknown[], total = items.length) => jsonResponse({ items, total, limit: 5, offset: 0 });
const recordsPage = (items: unknown[], total = items.length) => jsonResponse({ items, total, limit: 5, offset: 0 });

const EVENTS = [
  makeEvent({ id: "11111111-1111-4111-8111-111111111111", event_date: "2024-05-01" }),
  makeEvent({ id: "22222222-2222-4222-8222-222222222222", event_date: "2024-04-01" }),
];
const FILINGS = (VERTICAL.index.items as any[]).slice(0, 5);
const RECORD_ROWS = RECORD_ITEMS.slice(0, 5);

const OK: Required<Api> = {
  health: jsonResponse(HEALTH),
  ready: jsonResponse(READY),
  earnings: earningsPage(EVENTS, 7),
  filings: filingsPage(FILINGS, VERTICAL.index.total),
  records: recordsPage(RECORD_ROWS, RECORD_ITEMS.length),
};

const respond = (value: Reply | undefined): Promise<Response> =>
  value === undefined ? Promise.resolve(jsonResponse({}, 500)) : value instanceof Error ? Promise.reject(value) : Promise.resolve(value.clone());

/** Serves each endpoint the Overview may read; any other request fails the test loudly. */
function serve(api: Api = {}) {
  const routes = { ...OK, ...api };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const { pathname } = new URL(input);
      if (pathname === "/api/v1/health/ready") return respond(routes.ready);
      if (pathname === "/api/v1/health") return respond(routes.health);
      if (pathname === "/api/v1/earnings-events") return respond(routes.earnings);
      if (pathname === "/api/v1/filings") return respond(routes.filings);
      if (pathname === "/api/v1/research/records") return respond(routes.records);
      throw new Error(`The Overview must not request ${pathname}`);
    }),
  );
}

const urls = () => vi.mocked(fetch).mock.calls.map((call) => new URL(String(call[0])));
afterEach(() => vi.unstubAllGlobals());

describe("Overview: a launchpad, not a dashboard", () => {
  it("reads each source exactly once, each a bounded list read, and nothing else", async () => {
    serve();
    await renderServer(<Overview />);

    const requests = urls().map((url) => `${url.pathname}${url.search}`).sort();
    expect(requests).toEqual(
      [
        "/api/v1/earnings-events?limit=5&offset=0",
        "/api/v1/filings?limit=5&offset=0",
        "/api/v1/health",
        "/api/v1/health/ready",
        "/api/v1/research/records?limit=5&offset=0",
      ].sort(),
    );
  });

  it("opens with the research entry, then the four regions, in order", async () => {
    serve();
    await renderServer(<Overview />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const order = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
    expect(order).toEqual(["Start research", "Latest earnings events", "Latest filings", "Recent saved research", "System"]);
    expect(screen.getByRole("link", { name: "Open Research" })).toHaveAttribute("href", "/research");
    expect(screen.getByRole("link", { name: "Open Saved research" })).toHaveAttribute("href", "/research/saved");
    expect(screen.getByRole("link", { name: "Open Earnings" })).toHaveAttribute("href", "/earnings");
    expect(screen.getByRole("link", { name: "Open Filings" })).toHaveAttribute("href", "/filings");
  });

  it("shows the API's own rows and counts, each linking into a real workflow", async () => {
    serve();
    await renderServer(<Overview />);

    const earnings = screen.getByRole("table", { name: "Latest earnings events" });
    expect(within(earnings).getByRole("link", { name: /Earnings event Acme Corp, 2024-05-01/ })).toHaveAttribute("href", `/earnings/${EVENTS[0]!.id}`);
    expect(within(earnings).getAllByRole("link", { name: /^Research Acme Corp/ })[0]).toHaveAttribute("href", `/research/events/${EVENTS[0]!.id}`);
    // Counts are the API's `total`, never counted here.
    expect(screen.getByText("Showing 2 of 7 stored earnings events.")).toBeInTheDocument();

    const filings = screen.getByRole("table", { name: "Latest stored filings" });
    expect(within(filings).getAllByRole("link")).toHaveLength(FILINGS.length);
    expect(within(filings).getAllByRole("link")[0]).toHaveAttribute("href", expect.stringMatching(/^\/filings\/0000320193-24-/));

    const records = screen.getByRole("table", { name: "Recent saved research records" });
    expect(within(records).getAllByRole("link")[0]).toHaveAttribute("href", `/research/saved/${RECORD_ROWS[0].record_id}`);
    expect(screen.getByText(`Showing ${RECORD_ROWS.length} of ${RECORD_ITEMS.length} saved research records.`)).toBeInTheDocument();
    // The level is the backend's, shown by the same badge as on Saved research.
    expect(records.querySelectorAll("[data-reproducibility]")).toHaveLength(RECORD_ROWS.length);
  });

  it("every link leads to a real route, and none is a placeholder", async () => {
    serve();
    await renderServer(<Overview />);

    const known = [/^\/$/, /^\/research$/, /^\/research\/saved$/, /^\/research\/saved\/[0-9a-f-]{36}$/, /^\/research\/events\/[0-9a-f-]{36}$/, /^\/earnings$/, /^\/earnings\/[0-9a-f-]{36}$/, /^\/filings$/, /^\/filings\/[0-9-]+(\?.*)?$/];
    for (const link of screen.getAllByRole("link")) {
      const href = link.getAttribute("href")!;
      expect(known.some((pattern) => pattern.test(href)), href).toBe(true);
    }
  });

  it("makes one source's failure local: the others and the research entry stay", async () => {
    serve({ filings: jsonResponse({ detail: "Database unavailable." }, 503) });
    await renderServer(<Overview />);

    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent("Temporarily unavailable");
    expect(alerts[0]).toHaveTextContent("the latest filings");
    expect(screen.getByRole("table", { name: "Latest earnings events" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Recent saved research records" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Research" })).toBeInTheDocument();
    expect(screen.getAllByText("Reachable")).toHaveLength(2);
  });

  it("with every source down it invents nothing and leaks no address", async () => {
    const down = new TypeError("fetch failed");
    serve({ health: down, ready: down, earnings: down, filings: down, records: down });
    await renderServer(<Overview />);

    expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText(/^Showing /)).toBeNull();
    expect(screen.getByRole("link", { name: "Open Research" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/localhost|127\.0\.0\.1|8000|https?:/);
  });

  it("gives each region its own empty state, with no sample rows and no save prompt", async () => {
    serve({ earnings: earningsPage([]), filings: filingsPage([]), records: recordsPage([]) });
    await renderServer(<Overview />);

    expect(screen.getByText("No earnings events are stored")).toBeInTheDocument();
    expect(screen.getByText("No FSDS-backed filings are stored")).toBeInTheDocument();
    expect(screen.getByText("No saved research records are stored")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(document.body.textContent).not.toMatch(/save your first|create a baseline|save a study/i);
  });

  it("states a down dependency from the readiness body, never as healthy", async () => {
    serve({
      ready: jsonResponse(
        readyWith({
          status: "not_ready",
          dependencies: { database: { state: "unavailable", gating: true }, redis: { state: "ok", gating: false } },
          schema: { state: "unknown", expected: "0014", current: null, gating: true },
        }),
        503,
      ),
    });
    await renderServer(<Overview />);

    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.getByText("Not ready")).toBeInTheDocument();
  });

  it("treats Redis being down as an available application: nothing is shown as an outage", async () => {
    serve({
      ready: jsonResponse(
        readyWith({
          dependencies: { database: { state: "ok", gating: true }, redis: { state: "unavailable", gating: false } },
          detail: { redis: "ConnectionError" },
        }),
      ),
    });
    await renderServer(<Overview />);

    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.queryByText("Not ready")).toBeNull();
    expect(screen.getByText(/Unavailable · not required/)).toBeInTheDocument();
    const redisBadge = screen.getByText(/Unavailable · not required/).closest("[data-state]");
    expect(redisBadge?.getAttribute("data-state")).toBe("UNAVAILABLE");
    expect(redisBadge?.getAttribute("data-state")).not.toBe("ERROR"); // not an application error
    expect(screen.queryByText(/Dependency readiness could not be read/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says Redis is not configured rather than down when it is not configured", async () => {
    serve({
      ready: jsonResponse(
        readyWith({ dependencies: { database: { state: "ok", gating: true }, redis: { state: "not_configured", gating: false } } }),
      ),
    });
    await renderServer(<Overview />);

    expect(screen.getByText("Not configured")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("shows a schema mismatch only as 'Not ready': no revision or migration detail reaches a user", async () => {
    serve({
      ready: jsonResponse(
        readyWith({ status: "not_ready", schema: { state: "mismatch", expected: "0014", current: "0013", gating: true } }),
        503,
      ),
    });
    await renderServer(<Overview />);

    expect(screen.getByText("Not ready")).toBeInTheDocument();
    expect(screen.getAllByText("Reachable").length).toBeGreaterThan(0); // the database itself is fine
    expect(document.body.textContent).not.toMatch(/0013|0014|alembic|migration|schema|revision|mismatch/i);
  });

  it("fails strictly on a readiness body that does not match the contract", async () => {
    serve({ ready: jsonResponse({ ...READY, dependencies: { database: "ok", redis: "ok" } }) });
    await renderServer(<Overview />);

    expect(screen.getByText(/Dependency readiness could not be read/)).toBeInTheDocument();
    expect(screen.queryByText("Ready")).toBeNull();
  });

  it("claims nothing it does not read: no KPI, score or aggregate wording", async () => {
    serve();
    await renderServer(<Overview />);

    expect(document.body.textContent).not.toMatch(/\bAUM\b|portfolio|alpha|score|performance|return of|P&L|win rate|top performer|trending/i);
  });
});

describe("Overview source discipline", () => {
  const root = join(process.cwd(), "src");
  const files = [join(root, "app/page.tsx"), ...readdirSync(join(root, "components/overview")).map((name) => join(root, "components/overview", name))];
  const code = (file: string) =>
    readFileSync(file, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");

  it("computes nothing: no arithmetic on rows, no numeric coercion, no client-side aggregation", () => {
    for (const file of files) {
      expect(code(file), file).not.toMatch(/\bNumber\(|parseFloat|parseInt|toFixed|\.reduce\(|Math\.|\.sort\(|\.filter\(/);
    }
  });

  it("reads no detail endpoint and enriches no row", () => {
    for (const file of files) {
      expect(code(file), file).not.toMatch(/getEarningsEvent|getRecord\b|verifyRecord|getObservation|getFilingEps|lookupEvents/);
    }
  });

  it("is a server surface: no client component, no timer, no polling", () => {
    for (const file of files) {
      expect(code(file), file).not.toMatch(/^["']use client["']|setInterval|setTimeout|useEffect|useState/m);
    }
  });
});
