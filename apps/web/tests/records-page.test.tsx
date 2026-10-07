/* eslint-disable @typescript-eslint/no-explicit-any -- tests mutate deep, deliberately untyped payloads to model drift */
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SavedRecordPage from "@/app/research/saved/[recordId]/page";
import SavedResearchPage from "@/app/research/saved/page";
import { jsonResponse } from "./fixtures";
import { RECORDS, clone, record } from "./records-fixtures";
import { renderServer } from "./render-server";

vi.mock("next/navigation", () => ({
  usePathname: () => "/research/saved",
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
  redirect: (to: string) => {
    throw new Error(`NEXT_REDIRECT:${to}`);
  },
}));

type Json = Record<string, any>;
type Reply = Response | Error;

interface Api {
  list?: (url: URL) => Reply;
  detail?: (id: string) => Reply | undefined;
  verify?: (id: string) => Reply | undefined;
  event?: (id: string) => Reply | undefined;
}

const respond = (value: Reply) => (value instanceof Response ? Promise.resolve(value.clone()) : Promise.reject(value));
const byId = (id: string) => RECORDS.records.find((entry) => entry.record_id === id);

/** Serves the captured backend answers by URL. Anything a page should not request fails the test loudly. */
function serve(api: Api = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const url = new URL(input);
      const parts = url.pathname.split("/");
      if (url.pathname === "/api/v1/research/records") {
        if (api.list) return respond(api.list(url));
        const items = (RECORDS.lists.limit_20!.body as Json).items as Json[];
        const limit = Number(url.searchParams.get("limit"));
        const offset = Number(url.searchParams.get("offset"));
        return respond(jsonResponse({ items: items.slice(offset, offset + limit), total: items.length, limit, offset }));
      }
      if (url.pathname.startsWith("/api/v1/research/records/")) {
        const id = parts[5]!;
        const isVerify = parts[6] === "verify";
        const override = isVerify ? api.verify?.(id) : api.detail?.(id);
        if (override) return respond(override);
        const found = byId(id);
        if (!found) return respond(jsonResponse({ detail: "Research record not found." }, 404));
        const reply = isVerify ? found.verify! : (found.detail_after ?? found.detail);
        return respond(jsonResponse(reply.body, reply.status));
      }
      if (url.pathname.startsWith("/api/v1/earnings-events/")) {
        const id = parts[4]!;
        const override = api.event?.(id);
        if (override) return respond(override);
        const found = RECORDS.records.find((entry) => entry.event_id === id);
        const reply = found?.event_record_after ?? found?.event_record;
        return respond(reply ? jsonResponse(reply.body, reply.status) : jsonResponse({ detail: "Event not found." }, 404));
      }
      throw new Error(`unexpected request ${url.pathname}${url.search}`);
    }),
  );
}
const calls = (needle: string) => vi.mocked(fetch).mock.calls.map((c) => String(c[0])).filter((u) => u.includes(needle));
const allCalls = () => vi.mocked(fetch).mock.calls.map((c) => new URL(String(c[0])).pathname);

async function openList(search: Record<string, string> = {}) {
  await renderServer(await SavedResearchPage({ searchParams: Promise.resolve(search) } as never));
}
async function openRecord(name: string, search: Record<string, string> = {}) {
  const entry = record(name);
  await renderServer(await SavedRecordPage({ params: Promise.resolve({ recordId: entry.record_id }), searchParams: Promise.resolve(search) } as never));
  return entry;
}

beforeEach(() => serve());
afterEach(() => vi.unstubAllGlobals());

describe("the saved research list", () => {
  it("is a real list of the stored baselines: one labelled table, no placeholder copy", async () => {
    await openList();
    expect(screen.getByRole("heading", { level: 1, name: "Saved research" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/not part of the interface yet|placeholder/i);
    const region = screen.getByRole("region", { name: /Saved research baselines, scrollable/ });
    const table = within(region).getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + (RECORDS.lists.limit_20!.body as Json).items.length);
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Record", "Saved (UTC)", "Reproducibility", "Event (current record)", "Stored request", "Manifest fingerprint", "Result fingerprint"]);
  });

  it("states COMPLETE and PARTIAL as text, and lists a PARTIAL row's gaps without an alert", async () => {
    await openList();
    const table = screen.getByRole("table");
    expect(table.querySelectorAll('[data-reproducibility="COMPLETE"]').length).toBeGreaterThan(0);
    expect(table.querySelectorAll('[data-reproducibility="PARTIAL"]').length).toBe(3);
    expect(table).toHaveTextContent("Gaps: Event, Stock price bars, Benchmark levels");
    expect(table).toHaveTextContent("Accounting package");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the two fingerprints distinct, shortened for display and whole in the document", async () => {
    await openList();
    const first = (RECORDS.lists.limit_20!.body as Json).items[0] as Json;
    const row = screen.getByRole("link", { name: new RegExp(first.record_id.slice(0, 8)) }).closest("tr")!;
    const codes = [...row.querySelectorAll("code")];
    const manifest = codes.find((c) => c.getAttribute("title") === first.manifest_fingerprint)!;
    const result = codes.find((c) => c.getAttribute("title") === first.result_fingerprint)!;
    expect(manifest).toBeTruthy();
    expect(result).toBeTruthy();
    expect(manifest.textContent).toContain(first.manifest_fingerprint);
    expect(result.textContent).toContain(first.result_fingerprint);
    expect(first.manifest_fingerprint).not.toBe(first.result_fingerprint);
    expect(manifest.textContent!.replace("…", "")).toBe(first.manifest_fingerprint);
  });

  it("links each record to its own page by its full id", async () => {
    await openList();
    const first = (RECORDS.lists.limit_20!.body as Json).items[0] as Json;
    const link = screen.getByRole("link", { name: first.record_id });
    expect(link).toHaveAttribute("href", `/research/saved/${first.record_id}`);
  });

  it("requests the list once and one current-event lookup per distinct event: no detail, verification or observation", async () => {
    await openList();
    expect(calls("/research/records")).toHaveLength(1);
    expect(calls("/research/records")[0]).toMatch(/limit=20&offset=0$/);
    const items = (RECORDS.lists.limit_20!.body as Json).items as Json[];
    const distinct = new Set(items.map((item) => item.request.event_id));
    expect(calls("/earnings-events/")).toHaveLength(distinct.size);
    expect(distinct.size).toBeLessThanOrEqual(20);
    expect(allCalls().some((p) => /\/records\/|\/verify|\/observations\//.test(p))).toBe(false);
    expect(allCalls().every((p) => p.startsWith("/api/v1/research/records") || p.startsWith("/api/v1/earnings-events/"))).toBe(true);
  });

  it("reads a shared event ONCE however many records use it", async () => {
    const items = clone((RECORDS.lists.limit_20!.body as Json).items as Json[]).slice(0, 4);
    for (const item of items) item.request.event_id = items[0]!.request.event_id;
    serve({ list: (url) => jsonResponse({ items, total: 4, limit: Number(url.searchParams.get("limit")), offset: 0 }) });
    await openList();
    expect(calls("/earnings-events/")).toHaveLength(1);
  });

  it("labels the event as CURRENT metadata, apart from the stored fields", async () => {
    await openList();
    expect(screen.getByRole("columnheader", { name: "Event (current record)" })).toBeInTheDocument();
    expect(document.body.textContent).toMatch(/event as it is stored now, looked up for readability\. It is not part of a saved baseline/);
    // The stored request cell carries no company or ticker: those come only from the current lookup.
    const table = screen.getByRole("table");
    const requestCells = within(table).getAllByRole("cell").filter((cell) => cell.textContent?.includes("sessions") && cell.textContent.includes("Benchmark:"));
    expect(requestCells.length).toBeGreaterThan(0);
    for (const cell of requestCells) expect(cell.textContent).not.toMatch(/Observation Co|OBSV/);
  });

  it("a missing or unreadable event does not fail the list", async () => {
    const items = (RECORDS.lists.limit_20!.body as Json).items as Json[];
    const gone = record("event_missing");
    serve({ event: (id) => (id === gone.event_id ? jsonResponse({ detail: "Event not found." }, 404) : id === items[0]!.request.event_id ? new Error("network") : undefined) });
    await openList();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getAllByText("Missing").length).toBeGreaterThan(0);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("pages in the URL: page 2 asks for offset 20; a past-the-end page says so and links to the first", async () => {
    await openList({ page: "2" });
    expect(calls("/research/records")[0]).toMatch(/limit=20&offset=20$/);
    expect(screen.getByText("This page is past the last result")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the first page" })).toHaveAttribute("href", "/research/saved");
  });

  it("rows are not fabricated for an empty store, and there is no 'save your first' prompt", async () => {
    serve({ list: (url) => jsonResponse({ items: [], total: 0, limit: Number(url.searchParams.get("limit")), offset: 0 }) });
    await openList();
    expect(screen.getByText("No saved research baselines are stored")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("link", { name: "Open Research" })).toHaveAttribute("href", "/research");
    expect(document.body.textContent).not.toMatch(/save your first|create a baseline|new baseline/i);
  });

  it("says distinct things for 503, a network failure, contract drift and an integrity failure", async () => {
    const texts: string[] = [];
    for (const reply of [jsonResponse({ detail: "x" }, 503), new Error("down"), jsonResponse({ items: "nope" }), jsonResponse({ detail: "Stored research record failed integrity validation." }, 500)]) {
      serve({ list: () => reply });
      document.body.innerHTML = "";
      await openList();
      expect(screen.getByRole("alert")).toBeInTheDocument();
      texts.push(screen.getByRole("alert").textContent!);
      expect(screen.queryByRole("table")).toBeNull();
    }
    expect(new Set(texts).size).toBe(4);
    expect(texts[0]).toMatch(/Temporarily unavailable/);
    expect(texts[1]).toMatch(/Cannot reach/);
    expect(texts[2]).toMatch(/does not understand/);
    expect(texts[3]).toMatch(/unexpected error/);
    expect(texts[3]).toMatch(/fails its own integrity validation/);
    expect(texts[3]).not.toMatch(/canonical|manifest|SQL|stack/i);
  });

  it("an invalid page address is a local error with NO request", async () => {
    await openList({ page: "0" });
    expect(screen.getByRole("alert")).toHaveTextContent("This page address is not valid");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("redirects a non-canonical address before any request", async () => {
    await expect(openList({ page: "1" })).rejects.toThrow("NEXT_REDIRECT:/research/saved");
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("a saved record: the stored baseline", () => {
  it("is labelled STORED BASELINE and never called current, live or latest", async () => {
    await openRecord("complete_plain");
    const banner = screen.getByRole("note", { name: "Stored baseline" });
    expect(banner).toHaveTextContent("Stored baseline");
    expect(banner).toHaveTextContent("Immutable record");
    expect(banner.textContent).not.toMatch(/\b(current|live|latest)\b/i);
    expect(document.querySelector("main, body")!.textContent).not.toMatch(/current result|live result|latest result/i);
  });

  it("makes ONE record request by default: no verification, no event lookup, no live observation", async () => {
    const entry = await openRecord("complete_benchmark");
    expect(allCalls()).toEqual([`/api/v1/research/records/${entry.record_id}`]);
  });

  it("states the creation time as persistence metadata, not an event, evidence or filing time", async () => {
    await openRecord("complete_plain");
    const identity = screen.getByRole("region", { name: "Stored baseline identity" });
    expect(identity).toHaveTextContent("When the baseline was saved");
    expect(identity).toHaveTextContent("not the event time, the evidence time, a provider's availability time or a filing time");
  });

  it("keeps the record id, the manifest fingerprint and the result fingerprint apart, each whole and copyable", async () => {
    const entry = await openRecord("complete_plain");
    const detail = entry.detail.body as Json;
    const identity = screen.getByRole("region", { name: "Stored baseline identity" });
    for (const value of [detail.record_id, detail.manifest_fingerprint, detail.result_fingerprint]) {
      expect(within(identity).getByText(value)).toBeInTheDocument();
    }
    expect(within(identity).getAllByRole("button", { name: /^Copy / }).map((b) => b.textContent!.trim())).toHaveLength(3);
    expect(identity).toHaveTextContent("Identity of the request, methodology and consumed evidence. It is not the identity of the record.");
    expect(identity).toHaveTextContent("Identity of the stored result");
    expect(identity).toHaveTextContent("this page does not hash anything");
  });

  it("renders the stored request exactly: what was pinned and what was not", async () => {
    await openRecord("complete_filing_accounting");
    const request = screen.getByRole("region", { name: "Stored request" });
    expect(request).toHaveTextContent("+1, +2 sessions");
    expect(request).toHaveTextContent("0000320193-24-000010");
    expect(request).toHaveTextContent("Quarter");
    expect(request).toHaveTextContent("Diluted");
    expect(request).toHaveTextContent("SPLIT_ADJUSTED");
    expect(request).toHaveTextContent("DESCRIPTIVE, POINT_IN_TIME");
    // Not-pinned inputs are said to be not requested, never filled in.
    expect(request.querySelectorAll('[data-state="NOT_REQUESTED"]').length).toBeGreaterThanOrEqual(3);
  });

  it("a request with no benchmark and no filing says so (not 'unavailable')", async () => {
    await openRecord("complete_plain");
    const request = screen.getByRole("region", { name: "Stored request" });
    expect(within(request).getAllByText("Not requested").length).toBeGreaterThanOrEqual(5);
    expect(request).not.toHaveTextContent("Unavailable");
  });

  it("COMPLETE shows no gaps; PARTIAL lists each gap with its own sentence and is not an error", async () => {
    await openRecord("complete_plain");
    let section = screen.getByRole("region", { name: "Reproducibility" });
    expect(section.querySelector('[data-reproducibility="COMPLETE"]')).toBeTruthy();
    expect(section).toHaveTextContent("No gaps");
    document.body.innerHTML = "";

    await openRecord("partial_accounting_not_ingested");
    section = screen.getByRole("region", { name: "Reproducibility" });
    expect(section.querySelector('[data-reproducibility="PARTIAL"]')).toBeTruthy();
    expect(section).toHaveTextContent("Gaps (4)");
    for (const gap of ["EVENT_WITHOUT_INGESTION_RUN", "STOCK_BAR_WITHOUT_INGESTION_RUN", "FILING_WITHOUT_INGESTION_RUN", "ACCOUNTING_NOT_INGESTED"]) {
      expect(section).toHaveTextContent(gap);
    }
    expect(section).toHaveTextContent("no FSDS package carrying its facts is stored");
    expect(section).toHaveTextContent("The numbers are not wrong; the evidence trail is incomplete.");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(section.textContent).not.toMatch(/failed|incorrect|unusable|bad result/i);
    expect(section.querySelector("[data-state='ERROR']")).toBeNull();
  });

  it("keeps methodology identity apart from the fingerprints, and invents no residual assumptions", async () => {
    await openRecord("complete_plain");
    const section = screen.getByRole("region", { name: "Reproducibility" });
    expect(section).toHaveTextContent("Methodology identity");
    expect(section).toHaveTextContent("market-adjusted-simple/1");
    expect(section).toHaveTextContent("They are not the manifest fingerprint");
    // The record contract carries no residual assumptions, so none is listed (the badge's own sentence is the existing wording).
    expect(section.textContent).not.toMatch(/by convention|not snapshotted|unpinned external/i);
  });

  it("renders the stored result with the Research components: ladder, chart with its authoritative table, EPS surprise", async () => {
    await openRecord("complete_benchmark");
    const result = screen.getByRole("region", { name: "Stored result" });
    const descriptive = within(result).getByRole("region", { name: "DESCRIPTIVE" });
    expect(descriptive.querySelector("svg[role=img]")).toBeTruthy();
    const table = within(descriptive).getByRole("table", { name: /Stored descriptive returns by horizon/ });
    expect(table).toHaveTextContent("+10.00%");
    expect(table).toHaveTextContent("+7.50%");
    expect(descriptive).toHaveTextContent("Session alignment");
    expect(descriptive).toHaveTextContent("EPS surprise");
    expect(descriptive).toHaveTextContent("Selected in this request");
    expect(descriptive).not.toHaveTextContent("Selected by you");
    expect(within(descriptive).queryByRole("link", { name: "Select a benchmark" })).toBeNull();
  });

  it("shows the stored abnormal return as stored: a doctored stored value appears, nothing is recomputed", async () => {
    const entry = record("complete_benchmark");
    const doctored = clone(entry.detail.body as Json);
    doctored.result.descriptive.study.abnormal_returns[0].abnormal_return = "0.9";
    serve({ detail: () => jsonResponse(doctored) });
    await openRecord("complete_benchmark");
    expect(screen.getByRole("table", { name: /Stored descriptive returns by horizon/ })).toHaveTextContent("+90.00%");
  });

  it("a stored no-benchmark study says NOT_REQUESTED, with no 'select a benchmark' link", async () => {
    await openRecord("complete_plain");
    const descriptive = screen.getByRole("region", { name: "DESCRIPTIVE" });
    expect(descriptive).toHaveTextContent("No benchmark was selected. Nothing is chosen for you.");
    expect(within(descriptive).queryByRole("link", { name: /Select a benchmark/ })).toBeNull();
  });

  it("a stored PIT refusal does not affect the descriptive study; an eligible one shows its own study", async () => {
    await openRecord("complete_plain");
    let pit = screen.getByRole("region", { name: "POINT-IN-TIME" });
    expect(pit).toHaveTextContent("INELIGIBLE_NO_AVAILABILITY_EVIDENCE");
    expect(pit).toHaveTextContent("This does not affect the descriptive study above.");
    expect(pit.querySelector("svg[role=img]")).toBeNull();
    expect(screen.getByRole("region", { name: "DESCRIPTIVE" }).querySelector("svg[role=img]")).toBeTruthy();
    document.body.innerHTML = "";

    await openRecord("complete_pit_eligible");
    pit = screen.getByRole("region", { name: "POINT-IN-TIME" });
    expect(pit.querySelector("svg[role=img]")).toBeTruthy();
    expect(pit).toHaveTextContent("Eligibility is an evidence-policy result, not a recommendation.");
  });

  it("shows the stored filing link, PERIOD_UNVERIFIED, D0 and the EPS exactly as stored", async () => {
    await openRecord("complete_filing_accounting");
    const result = screen.getByRole("region", { name: "Stored result" });
    expect(result).toHaveTextContent("PERIOD_UNVERIFIED");
    expect(result).toHaveTextContent("The filing's fiscal period was not checked against this event.");
    expect(result).toHaveTextContent("D0 · a stored filing exists");
    expect(result).toHaveTextContent("It implies no time, no first public disclosure and no availability");
    expect(result).toHaveTextContent("1.5300");
    expect(result).toHaveTextContent("1.5200");
    expect(result).toHaveTextContent("+0.0100");
    expect(result).toHaveTextContent("As presented in this filing");
    expect(result).toHaveTextContent("+0.66%");
    expect(result).not.toHaveTextContent(/verified period|Earnings filing|Related filing/);
  });

  it("keeps a stored accounting refusal's own status (NOT_INGESTED) and shows no invented figure", async () => {
    await openRecord("partial_accounting_not_ingested");
    const result = screen.getByRole("region", { name: "Stored result" });
    expect(result).toHaveTextContent("NOT_INGESTED");
    expect(result).toHaveTextContent("CURRENT_UNAVAILABLE");
    expect(result).toHaveTextContent("INPUT_UNAVAILABLE");
    expect(result).toHaveTextContent("Not computed");
  });

  it("lists the evidence the manifest pinned and opens it in the evidence drawer with the stored identifiers", async () => {
    await openRecord("complete_filing_accounting");
    const evidence = screen.getByRole("region", { name: "Evidence and provenance" });
    const rows = within(evidence).getAllByRole("row").slice(1).map((row) => row.querySelector("th, td")?.textContent);
    expect(rows).toEqual(["Event", "Listing resolution", "Point-in-time inputs", "Stock prices", "Benchmark prices", "Filing", "Accounting (FSDS)"]);
    for (const subject of ["event", "listing resolution", "stock prices", "benchmark prices", "filing", "accounting (fsds)"]) {
      expect(within(evidence).getByRole("button", { name: `Evidence for the baseline's ${subject}` })).toBeInTheDocument();
    }
    expect(evidence).toHaveTextContent("Pinned");
  });

  it("offers verification and a live re-run as links, and issues nothing for either", async () => {
    const entry = await openRecord("complete_filing_accounting");
    const verify = screen.getByRole("link", { name: "Verify against current evidence" });
    expect(verify).toHaveAttribute("href", `/research/saved/${entry.record_id}?verify=1#verification`);
    const rerun = screen.getByRole("link", { name: "Re-run this request live" });
    expect(rerun.getAttribute("href")).toMatch(new RegExp(`^/research/events/${entry.event_id}\\?horizons=1,2&benchmark_id=`));
    expect(rerun.getAttribute("href")).not.toContain(entry.record_id);
    expect(calls("/verify")).toHaveLength(0);
    expect(calls("/observations/")).toHaveLength(0);
  });

  it("has no save, edit, delete, rename or mutation affordance anywhere", async () => {
    await openRecord("complete_plain");
    expect(screen.queryByRole("button", { name: /save|edit|delete|rename|update|refresh|replace/i })).toBeNull();
    expect(document.querySelector("form, input, textarea, select")).toBeNull();
    expect(document.body.textContent).not.toMatch(/save baseline|save this|update baseline|refresh baseline|replace result/i);
  });
});

describe("a saved record: failures of the record read", () => {
  it("a malformed id and an unknown id are not-found; the malformed one makes no request", async () => {
    await expect(SavedRecordPage({ params: Promise.resolve({ recordId: "not-a-uuid" }), searchParams: Promise.resolve({}) } as never)).rejects.toThrow("NEXT_NOT_FOUND");
    expect(fetch).not.toHaveBeenCalled();
    await expect(
      SavedRecordPage({ params: Promise.resolve({ recordId: RECORDS.unknown_record.record_id }), searchParams: Promise.resolve({}) } as never),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("an upper-case id is redirected to the lower-case address before any request", async () => {
    const entry = record("complete_plain");
    await expect(
      SavedRecordPage({ params: Promise.resolve({ recordId: entry.record_id.toUpperCase() }), searchParams: Promise.resolve({}) } as never),
    ).rejects.toThrow(`NEXT_REDIRECT:/research/saved/${entry.record_id}`);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("the lower-casing redirect keeps an explicit verify request", async () => {
    const entry = record("complete_plain");
    await expect(
      SavedRecordPage({ params: Promise.resolve({ recordId: entry.record_id.toUpperCase() }), searchParams: Promise.resolve({ verify: "1" }) } as never),
    ).rejects.toThrow(`NEXT_REDIRECT:/research/saved/${entry.record_id}?verify=1`);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("a changed status with an empty path list still shows the status", async () => {
    const bad = clone(record("manifest_changed").verify!.body as Json);
    bad.changed_paths = [];
    serve({ verify: () => jsonResponse(bad) });
    await openRecord("manifest_changed", { verify: "1" });
    expect(document.querySelector('[data-verification="MANIFEST_CHANGED"]')).toBeTruthy();
    expect(screen.getByRole("region", { name: "Verification result" })).toHaveTextContent("Changed manifest paths (0)");
  });

  it("a stored record that fails integrity validation (500) is a safe server failure: not 'not found', not a status", async () => {
    await openRecord("corrupt_manifest");
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The server hit an unexpected error");
    expect(alert).toHaveTextContent("fails its own integrity validation");
    expect(alert).toHaveTextContent("is not a verification result");
    expect(alert.textContent).not.toMatch(/not found|MATCH|PARTIAL|canonical|hash does not/i);
    expect(screen.queryByRole("note", { name: "Stored baseline" })).toBeNull();
  });

  it("503, a network failure and a contract failure are each their own state", async () => {
    const texts: string[] = [];
    for (const reply of [jsonResponse({}, 503), new Error("x"), jsonResponse({ record_id: "x" })]) {
      serve({ detail: () => reply });
      document.body.innerHTML = "";
      await openRecord("complete_plain");
      texts.push(screen.getByRole("alert").textContent!);
    }
    expect(new Set(texts).size).toBe(3);
  });

  it("an invalid verify parameter shows the baseline and an explanation, and runs nothing", async () => {
    await openRecord("complete_plain", { verify: "yes" });
    expect(screen.getByRole("note", { name: "Stored baseline" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Verification was not run");
    expect(calls("/verify")).toHaveLength(0);
  });
});

describe("verification (explicit, ?verify=1)", () => {
  const STATUSES: [string, string, string][] = [
    ["complete_plain", "MATCH", "reproduces the stored baseline"],
    ["partial_benchmark", "EVIDENCE_INCOMPLETE", "is not a change in the result"],
    ["manifest_changed", "MANIFEST_CHANGED", "differs from the stored baseline's manifest"],
    ["result_changed", "RESULT_CHANGED", "produces a different result"],
  ];

  it.each(STATUSES)("%s renders the API's status %s and the stored baseline stays", async (name, status, sentence) => {
    const entry = await openRecord(name, { verify: "1" });
    const result = screen.getByRole("region", { name: "Verification result" });
    expect(result.querySelector(`[data-verification="${status}"]`)).toBeTruthy();
    expect(result).toHaveTextContent(sentence);
    expect(result).toHaveTextContent("does not change, replace or refresh the saved record");
    expect(screen.getByRole("note", { name: "Stored baseline" })).toBeInTheDocument();
    expect(allCalls()).toEqual([`/api/v1/research/records/${entry.record_id}`, `/api/v1/research/records/${entry.record_id}/verify`]);
    expect(calls("/observations/")).toHaveLength(0);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("MATCH is not worded as universal correctness; EVIDENCE_INCOMPLETE is not a change", async () => {
    await openRecord("complete_plain", { verify: "1" });
    const match = screen.getByRole("region", { name: "Verification result" });
    expect(match).toHaveTextContent("It does not show the result is correct");
    expect(match.textContent).not.toMatch(/\b(universally|always) correct\b/i);
    document.body.innerHTML = "";
    await openRecord("partial_benchmark", { verify: "1" });
    const incomplete = screen.getByRole("region", { name: "Verification result" });
    expect(incomplete).toHaveTextContent("not a complete reproducibility claim");
    expect(incomplete.querySelector("[data-verification]")!.getAttribute("data-verification")).not.toBe("RESULT_CHANGED");
    expect(incomplete).not.toHaveTextContent("Changed result paths");
  });

  it("shows the backend's changed manifest and result paths, grouped, exactly as supplied", async () => {
    await openRecord("manifest_changed", { verify: "1" });
    const result = screen.getByRole("region", { name: "Verification result" });
    expect(result).toHaveTextContent("Changed manifest sections");
    expect(result).toHaveTextContent("Evidence");
    const verification = record("manifest_changed").verify!.body as Json;
    for (const path of [...verification.changed_paths, ...verification.changed_result_paths]) {
      expect(within(result).getByText(path)).toBeInTheDocument();
    }
    expect(within(result).getAllByText(/^evidence\.stock$|^descriptive$/).length).toBeGreaterThan(0);
    expect(result.textContent).toMatch(/does not compare financial values/);
    await expect(Promise.resolve(document.querySelectorAll("#verification-result pre").length)).resolves.toBe(0);
  });

  it("RESULT_CHANGED lists result paths only and names no manifest change", async () => {
    await openRecord("result_changed", { verify: "1" });
    const result = screen.getByRole("region", { name: "Verification result" });
    expect(within(result).getByText("event.reported_eps")).toBeInTheDocument();
    expect(result).not.toHaveTextContent("Changed manifest sections");
    expect(result).not.toHaveTextContent("Changed manifest paths");
  });

  it("shows both sides' levels, gaps and all five fingerprints, with no sameness mark of our own", async () => {
    await openRecord("manifest_changed", { verify: "1" });
    const result = screen.getByRole("region", { name: "Verification result" });
    const verification = record("manifest_changed").verify!.body as Json;
    for (const side of ["expected", "actual"]) {
      for (const key of ["request", "methodology", "evidence", "manifest", "result"]) {
        expect(within(result).getAllByText(verification[side].fingerprints[key]).length).toBeGreaterThan(0);
      }
    }
    expect(result.textContent).not.toMatch(/\b(identical|unchanged|differs: yes|✓|✗)\b/);
  });

  it("409 (the event is gone) is its own failure, the baseline is unaffected, and it is not a status", async () => {
    await openRecord("event_missing", { verify: "1" });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("This record cannot be re-run");
    expect(alert).toHaveTextContent("no longer stored");
    expect(screen.getByRole("note", { name: "Stored baseline" })).toBeInTheDocument();
    expect(document.querySelector("[data-verification]")).toBeNull();
  });

  it("500 on verification is a safe failure with no verification status and an intact baseline", async () => {
    serve({ verify: () => jsonResponse({ detail: "Stored research record failed integrity validation." }, 500) });
    await openRecord("complete_plain", { verify: "1" });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The server hit an unexpected error");
    expect(alert).toHaveTextContent("is not a verification result");
    expect(document.querySelector("[data-verification]")).toBeNull();
    expect(screen.getByRole("note", { name: "Stored baseline" })).toBeInTheDocument();
  });

  it("503, a timeout and contract drift on verification are each distinct and leave the baseline", async () => {
    const texts: string[] = [];
    const timeout = new DOMException("t", "TimeoutError");
    for (const reply of [jsonResponse({}, 503), timeout as unknown as Error, jsonResponse({ ...(record("complete_plain").verify!.body as Json), status: "WHATEVER" })]) {
      serve({ verify: () => reply });
      document.body.innerHTML = "";
      await openRecord("complete_plain", { verify: "1" });
      texts.push(screen.getByRole("alert").textContent!);
      expect(screen.getByRole("note", { name: "Stored baseline" })).toBeInTheDocument();
    }
    expect(new Set(texts).size).toBe(3);
    expect(texts[1]).toMatch(/timed out/);
  });

  it("a verification contradicting its own status is refused as drift and never shown", async () => {
    const bad = clone(record("complete_plain").verify!.body as Json);
    bad.changed_result_paths = ["event.reported_eps"];
    serve({ verify: () => jsonResponse(bad) });
    await openRecord("complete_plain", { verify: "1" });
    expect(screen.getByRole("alert")).toHaveTextContent("does not understand");
    expect(document.querySelector("[data-verification]")).toBeNull();
  });
});
