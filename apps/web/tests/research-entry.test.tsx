import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "@testing-library/react";

import EarningsDetailPage from "@/app/earnings/[id]/page";
import ResearchPage from "@/app/research/page";
import { MarketReactionPreview } from "@/components/research/market-reaction-preview";
import { jsonResponse, makeEvent } from "./fixtures";
import { CAPTURE, captured } from "./research-fixtures";
import { renderServer } from "./render-server";

vi.mock("next/navigation", () => ({
  usePathname: () => "/research",
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
  redirect: (to: string) => {
    throw new Error(`NEXT_REDIRECT:${to}`);
  },
}));

const listProps = (search: Record<string, string | string[]> = {}) => ({ searchParams: Promise.resolve(search), params: Promise.resolve({}) }) as never;
const apiCalls = (needle: string) => vi.mocked(fetch).mock.calls.map((c) => String(c[0])).filter((u) => u.includes(needle));
const page = (items: unknown[], total = items.length, offset = 0) => jsonResponse({ items, total, limit: 50, offset });
const records = () => CAPTURE.cases.slice(0, 3).map((c) => c.event_record);

beforeEach(() => vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(page(records())))));
afterEach(() => vi.unstubAllGlobals());

describe("research entry (/research)", () => {
  it("explains what a study is, finds events with one request, and chooses no event for the reader", async () => {
    await renderServer(await ResearchPage(listProps()));
    expect(apiCalls("/earnings-events")).toHaveLength(1);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Research");
    expect(screen.getByText(/A study starts from one stored earnings event/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Stored earnings events" });
    expect(within(table).getAllByRole("link", { name: /^Research / })).toHaveLength(3);
  });

  it("each Research link names its event and carries nothing else: no benchmark, no filing, no horizons", async () => {
    await renderServer(await ResearchPage(listProps()));
    const links = screen.getAllByRole("link", { name: /^Research / });
    links.forEach((link, index) => {
      expect(link).toHaveAttribute("href", `/research/events/${records()[index]!.id}`);
    });
    expect(document.body.textContent).not.toMatch(/benchmark_id|filing_accession/);
  });

  it("uses the shared filters and canonical URLs on its own path", async () => {
    await expect(ResearchPage(listProps({ ticker: "", page: "1" }))).rejects.toThrow("NEXT_REDIRECT:/research");
    await renderServer(await ResearchPage(listProps({ ticker: "OBSV" })));
    expect(apiCalls("/earnings-events")[0]).toContain("ticker=OBSV");
    expect(screen.getByRole("form", { name: "Find an event" })).toHaveAttribute("action", "/research");
    expect(screen.getByRole("link", { name: "Clear" })).toHaveAttribute("href", "/research");
  });

  it("rejects an invalid filter locally with no request", async () => {
    await renderServer(await ResearchPage(listProps({ date_from: "yesterday" })));
    expect(screen.getByRole("alert")).toHaveTextContent("These filters cannot be used");
    expect(apiCalls("/earnings-events")).toHaveLength(0);
  });

  it("pages with links on /research and tells the empty cases apart", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(page(records(), 120, 50))));
    await renderServer(await ResearchPage(listProps({ page: "2" })));
    expect(screen.getByRole("link", { name: "Next page" })).toHaveAttribute("href", "/research?page=3");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(page([]))));
    const { unmount } = await renderServer(await ResearchPage(listProps()));
    expect(screen.getByRole("status")).toHaveTextContent("No earnings events are stored");
    unmount();
    await renderServer(await ResearchPage(listProps({ ticker: "ZZZ" })));
    expect(screen.getByRole("status")).toHaveTextContent("Nothing matches these filters");
  });

  it("a failed read is a safe error, never an empty finder", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("connect ECONNREFUSED 127.0.0.1:8000"))));
    await renderServer(await ResearchPage(listProps()));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Cannot reach the QuantForge API");
    expect(alert.textContent).not.toMatch(/127|8000/);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("invents no research history, links to Saved research, and keeps the principles behind a disclosure", async () => {
    await renderServer(await ResearchPage(listProps()));
    expect(screen.getByRole("link", { name: "Saved research" })).toHaveAttribute("href", "/research/saved");
    expect(document.body.textContent).not.toMatch(/recent studies|your studies|history of/i);
    const how = screen.getByText("How studies work").closest("details")!;
    expect(how).not.toHaveAttribute("open");
    expect(within(how).getByText("Descriptive versus point-in-time")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/sharpe|p&l|equity curve|\d+(\.\d+)?%/i);
  });
});

describe("the event record's stock-only market-reaction preview", () => {
  const ID = captured("default_no_benchmark").event_id;
  const routeApi = () =>
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const url = new URL(input);
        if (url.pathname.includes("/research/observations/")) return Promise.resolve(jsonResponse(captured("default_no_benchmark").response));
        return Promise.resolve(jsonResponse({ ...captured("default_no_benchmark").event_record, id: ID }));
      }),
    );

  it("requests the DESCRIPTIVE default study only: no benchmark, no filing, no horizons", async () => {
    routeApi();
    await renderServer(await EarningsDetailPage({ params: Promise.resolve({ id: ID }) } as never));
    const [observation] = apiCalls("/research/observations");
    expect(apiCalls("/research/observations")).toHaveLength(1);
    expect(new URL(observation!).search).toBe("");
  });

  it("shows the ladder and a stock-only returns table, from the same components the workspace uses", async () => {
    routeApi();
    await renderServer(await EarningsDetailPage({ params: Promise.resolve({ id: ID }) } as never));
    const frame = screen.getByRole("region", { name: "Market reaction" });
    expect(within(frame).getByRole("list", { name: "Stored trading sessions" })).toBeInTheDocument();
    const table = within(frame).getByRole("table", { name: "Stock returns by horizon" });
    expect(within(table).getByText("+10.00%")).toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: /Benchmark|Abnormal/ })).toBeNull();
    expect(frame).toHaveTextContent("No benchmark is selected and no filing is linked here.");
  });

  it("stays a preview: no chart, no point-in-time verdict, no ledger, and a link into the workspace", async () => {
    routeApi();
    await renderServer(await EarningsDetailPage({ params: Promise.resolve({ id: ID }) } as never));
    const frame = screen.getByRole("region", { name: "Market reaction" });
    expect(frame.querySelector("svg[role=img]")).toBeNull();
    expect(within(frame).queryByText(/POINT-IN-TIME|Refusal ledger/)).toBeNull();
    expect(within(frame).getByRole("link", { name: "Open the research workspace" })).toHaveAttribute("href", `/research/events/${ID}`);
    // the primary action is still Open research
    expect(screen.getAllByRole("link", { name: /Open Research for/ }).length).toBeGreaterThan(0);
  });

  it("a refused study (unknown timing) shows the backend's reason in the preview, not an error", async () => {
    const refused = JSON.parse(JSON.stringify(captured("default_no_benchmark").response));
    refused.descriptive.study.alignment.session_alignment = {
      status: "UNKNOWN_TIMING",
      event_session: null,
      first_post_event_session: null,
      reason: "source declared no timing; before- vs after-market cannot be inferred",
    };
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(refused))));
    await renderServer(await MarketReactionPreview({ eventId: captured("default_no_benchmark").event_id }));
    expect(screen.getByRole("status")).toHaveTextContent("source declared no timing");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a failed or drifted preview is isolated and safe", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ detail: "Database unavailable." }, 503))));
    await renderServer(await MarketReactionPreview({ eventId: ID }));
    expect(screen.getByRole("alert")).toHaveTextContent("Temporarily unavailable");
  });
});

describe("ordinary reads keep the 4 s class (only the observation is longer)", () => {
  it("the earnings finder uses the default timeout", async () => {
    const spy = vi.spyOn(AbortSignal, "timeout");
    await renderServer(await ResearchPage(listProps()));
    expect(spy).toHaveBeenCalledWith(4000);
    expect(spy).not.toHaveBeenCalledWith(10_000);
  });
});

void makeEvent;
void render;
void userEvent;
