/* eslint-disable @typescript-eslint/no-explicit-any -- tests mutate deep, deliberately untyped payloads to model drift */
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ResearchEventPage from "@/app/research/events/[eventId]/page";
import { jsonResponse } from "./fixtures";
import { BENCHMARK_ID, CAPTURE, EMPTY_BENCHMARK_ID, INACTIVE_BENCHMARK_ID, captured, clone } from "./research-fixtures";
import { renderServer } from "./render-server";

vi.mock("next/navigation", () => ({
  usePathname: () => "/research/events/x",
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
  /** Replaces the observation answer (a Response, or an Error for a transport failure). */
  observation?: (url: URL) => Reply;
  benchmarks?: Reply;
  record?: Reply;
}

/** Serves the captured backend answers by URL: an observation request with no captured answer fails the test. */
function serve(api: Api = {}) {
  const respond = (value: Reply) => (value instanceof Error ? Promise.reject(value) : Promise.resolve(value.clone()));
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const url = new URL(input);
      if (url.pathname === "/api/v1/benchmarks") {
        if (api.benchmarks) return respond(api.benchmarks);
        return respond(
          jsonResponse({
            ...CAPTURE.benchmarks,
            limit: Number(url.searchParams.get("limit")),
            offset: Number(url.searchParams.get("offset")),
          }),
        );
      }
      if (url.pathname.startsWith("/api/v1/research/observations/")) {
        const eventId = url.pathname.split("/").pop();
        if (api.observation) return respond(api.observation(url));
        const wanted = Object.fromEntries(url.searchParams);
        const found = CAPTURE.cases.find((c) => c.event_id === eventId && JSON.stringify(sorted(c.params)) === JSON.stringify(sorted(wanted)));
        if (!found) throw new Error(`no captured observation for ${url.pathname}${url.search}`);
        return respond(jsonResponse(found.response));
      }
      if (url.pathname.startsWith("/api/v1/earnings-events/")) {
        if (api.record) return respond(api.record);
        const eventId = url.pathname.split("/").pop();
        const found = CAPTURE.cases.find((c) => c.event_id === eventId);
        return respond(found ? jsonResponse(found.event_record) : jsonResponse({ detail: "Event not found." }, 404));
      }
      return respond(jsonResponse({}, 500));
    }),
  );
}
const sorted = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)));
const calls = (needle: string) => vi.mocked(fetch).mock.calls.map((c) => String(c[0])).filter((u) => u.includes(needle));

async function open(name: string, search: Record<string, string> = {}) {
  const c = captured(name);
  await renderServer(
    await ResearchEventPage({
      params: Promise.resolve({ eventId: c.event_id }),
      searchParams: Promise.resolve(search),
    } as never),
  );
  return c;
}
/** The address parameters that reproduce a captured request. */
const searchOf = (name: string) => captured(name).params;

beforeEach(() => serve());
afterEach(() => vi.unstubAllGlobals());

const descriptive = () => screen.getByRole("region", { name: "DESCRIPTIVE" });
const pit = () => screen.getByRole("region", { name: "POINT-IN-TIME" });

describe("no benchmark (the default study)", () => {
  it("runs the default study and sends no benchmark, no filing and no horizons", async () => {
    await open("default_no_benchmark");
    const [url] = calls("/research/observations");
    expect(calls("/research/observations")).toHaveLength(1);
    expect(new URL(url!).search).toBe("");
    expect(descriptive()).toBeInTheDocument();
  });

  it("explains that no benchmark was requested, as NOT_REQUESTED and not as a failure", async () => {
    await open("default_no_benchmark");
    const notice = within(descriptive()).getByRole("status", { name: /Benchmark-relative and abnormal returns: Not requested/ });
    expect(notice).toHaveTextContent("No benchmark was selected. Nothing is chosen for you.");
    expect(within(notice).getByRole("link", { name: "Select a benchmark" })).toHaveAttribute("href", "#research-config");
    expect(within(descriptive()).queryByRole("alert")).toBeNull();
  });

  it("shows benchmark and abnormal cells as not requested while the stock returns stay valid", async () => {
    await open("single_horizon", { horizons: "1" });
    const table = within(descriptive()).getByRole("table", { name: "Descriptive returns by horizon" });
    const row = within(table).getAllByRole("row")[1]!;
    expect(within(row).getByText("+10.00%")).toBeInTheDocument();
    const marks = [...row.querySelectorAll("[data-state]")].map((el) => el.getAttribute("data-state"));
    expect(marks.filter((state) => state === "NOT_REQUESTED")).toHaveLength(2);
    expect(row.querySelector('[data-state="UNAVAILABLE"]')).toBeNull();
  });

  it("with no benchmark the chart has stock marks only and says so", async () => {
    await open("single_horizon", { horizons: "1" });
    const chart = within(descriptive()).getByRole("img", { name: /Descriptive return by horizon/ });
    expect(chart.querySelectorAll("circle")).toHaveLength(1);
    expect(chart.querySelectorAll("path")).toHaveLength(0);
    expect(descriptive()).toHaveTextContent("No benchmark selected: stock returns only.");
  });

  it("states the stock-only partial study honestly when horizons exceed the stored sessions", async () => {
    await open("default_no_benchmark");
    const table = within(descriptive()).getByRole("table", { name: "Descriptive returns by horizon" });
    expect(within(table).getAllByText(/Too few stored sessions/)).toHaveLength(2);
    expect(within(descriptive()).getByText("Partial")).toBeInTheDocument();
    expect(descriptive().querySelector('[data-state="MISSING"]')).not.toBeNull();
  });
});

describe("explicit benchmark", () => {
  const search = () => searchOf("benchmark_selected");

  it("sends the benchmark only because it is in the address, and shows its name", async () => {
    await open("benchmark_selected", search());
    const [url] = calls("/research/observations");
    expect(new URL(url!).searchParams.get("benchmark_id")).toBe(BENCHMARK_ID);
    expect(descriptive()).toHaveTextContent("Observation Index (OBSI)");
  });

  it("shows the backend's stock, benchmark and abnormal returns without recomputing any of them", async () => {
    const c = captured("benchmark_selected");
    const doctored = clone(c.response) as unknown as Json;
    // An abnormal return that is NOT stock minus benchmark: React must show the backend's figure.
    doctored.descriptive.study.abnormal_returns[0].abnormal_return = "0.9";
    serve({ observation: () => jsonResponse(doctored) });
    await open("benchmark_selected", search());
    const row = within(within(descriptive()).getByRole("table", { name: "Descriptive returns by horizon" })).getAllByRole("row")[1]!;
    expect(within(row).getByText("+10.00%")).toBeInTheDocument();
    expect(within(row).getByText("+2.50%")).toBeInTheDocument();
    expect(within(row).getByText("+90.00%")).toBeInTheDocument();
    expect(row.textContent).not.toContain("+7.50%");
  });

  it("keeps the exact ratio of every return available to assistive technology", async () => {
    await open("benchmark_selected", search());
    expect(descriptive().querySelector('[data-exact="0.1"]')).not.toBeNull();
    expect(descriptive().querySelector('[data-exact="0.075"]')).not.toBeNull();
  });

  it("an unsorted request is redirected to the canonical ascending form before any read", async () => {
    await expect(open("custom_horizons_unsorted", searchOf("custom_horizons_unsorted"))).rejects.toThrow(
      `NEXT_REDIRECT:/research/events/${captured("custom_horizons_unsorted").event_id}?horizons=1,2&benchmark_id=${BENCHMARK_ID}`,
    );
    expect(calls("/research/observations")).toHaveLength(0);
  });

  it("a benchmark with no stored data refuses ITS part and leaves the stock study visible", async () => {
    await open("benchmark_without_data", searchOf("benchmark_without_data"));
    const table = within(descriptive()).getByRole("table", { name: "Descriptive returns by horizon" });
    const row = within(table).getAllByRole("row")[1]!;
    expect(within(row).getByText("+10.00%")).toBeInTheDocument(); // stock
    expect(row.querySelector('[data-state="MISSING"]')).not.toBeNull(); // benchmark
    const chart = within(descriptive()).getByRole("img");
    expect(chart.querySelectorAll("circle").length).toBe(2);
    expect(chart.querySelectorAll("path").length).toBe(0); // no benchmark marks, no interpolation
    expect(chart.textContent).toMatch(/Benchmark: Missing anchor price/);
    const ledger = screen.getByRole("table", { name: /Benchmark · 2 refusals/ });
    expect(within(ledger).getAllByText("MISSING_ANCHOR_PRICE")).toHaveLength(2);
  });
});

describe("inactive benchmarks", () => {
  it("lists an inactive benchmark honestly, selectable, in its own group", async () => {
    await open("inactive_benchmark", searchOf("inactive_benchmark"));
    const select = screen.getByLabelText("Benchmark") as HTMLSelectElement;
    const group = select.querySelector("optgroup")!;
    expect(group.label).toMatch(/Inactive/);
    expect(within(group as unknown as HTMLElement).getByText(/Retired Index \(RETD\) · USD · inactive/)).toBeInTheDocument();
    expect(select.value).toBe(INACTIVE_BENCHMARK_ID);
  });

  it("explains inactive without implying the history is invalid, and labels the chip", async () => {
    await open("inactive_benchmark", searchOf("inactive_benchmark"));
    expect(screen.getByRole("note", { name: "Inactive benchmark" })).toHaveTextContent("no longer used for new research, retained for history");
    expect(screen.getByRole("note", { name: "Inactive benchmark" })).toHaveTextContent("does not mean its stored history is invalid");
    expect(screen.getByRole("region", { name: "Research context" })).toHaveTextContent("Retired Index (RETD) · inactive");
  });
});

describe("benchmark discovery", () => {
  it("never selects, recommends or defaults a benchmark: the initial state is None", async () => {
    await open("default_no_benchmark");
    const select = screen.getByLabelText("Benchmark") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(within(select).getAllByRole("option")[0]).toHaveTextContent("None (stock returns only)");
    expect(document.body.textContent).not.toMatch(/recommended|SPY|suggested/i);
    expect(screen.getAllByText(/Nothing is chosen for you/).length).toBeGreaterThan(0);
  });

  it("lists active benchmarks first, then inactive, and disambiguates equal names by id", async () => {
    const twin = clone(CAPTURE.benchmarks) as Json;
    twin.items.push({ ...twin.items[0], id: "aaaaaaaa-1111-4111-8111-111111111111" });
    serve({ benchmarks: jsonResponse({ ...twin, total: 4, limit: 200, offset: 0 }) });
    await open("default_no_benchmark");
    const select = screen.getByLabelText("Benchmark") as HTMLSelectElement;
    const labels = [...select.options].map((o) => o.textContent);
    expect(labels.findIndex((t) => /Retired Index/.test(t ?? ""))).toBe(labels.length - 1);
    expect(labels.filter((t) => /Empty Index/.test(t ?? "")).every((t) => /· id [0-9a-f]{8}/.test(t ?? ""))).toBe(true);
  });

  it("an empty index says so, and the stock study still runs", async () => {
    serve({ benchmarks: jsonResponse({ items: [], total: 0, limit: 200, offset: 0 }) });
    await open("default_no_benchmark");
    expect(screen.getByText("No benchmarks are stored.")).toBeInTheDocument();
    expect(descriptive()).toBeInTheDocument();
  });

  it("a failed benchmark read does not take the observation down: it says so, safely, and keeps a selected id", async () => {
    serve({ benchmarks: jsonResponse({ detail: "Database unavailable." }, 503) });
    await open("benchmark_selected", searchOf("benchmark_selected"));
    expect(screen.getByText(/The benchmark list could not be read/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Remove the selected benchmark" })).toHaveAttribute("href", `/research/events/${captured("benchmark_selected").event_id}?horizons=1,2`);
    expect(document.querySelector('input[type="hidden"][name="benchmark_id"]')).toHaveValue(BENCHMARK_ID);
    // the study itself is intact, with the benchmark named by its id (the list is unavailable)
    expect(within(descriptive()).getByRole("table", { name: "Descriptive returns by horizon" })).toBeInTheDocument();
    expect(descriptive()).toHaveTextContent(BENCHMARK_ID);
    expect(screen.queryByText(/ECONN|localhost|8000/)).toBeNull();
  });

  it("does not treat the first page as the whole universe: it states the paging and offers the next page", async () => {
    const page = clone(CAPTURE.benchmarks) as Json;
    serve({
      benchmarks: jsonResponse({ ...page, total: 450, limit: 200, offset: 0 }),
    });
    await open("default_no_benchmark");
    const paging = screen.getByTestId("benchmark-paging");
    expect(paging).toHaveTextContent("Showing benchmarks 1–3 of 450 stored");
    expect(paging).toHaveTextContent("one page of the index, not a search");
    expect(within(paging).getByRole("link", { name: "Next benchmarks" })).toHaveAttribute("href", `/research/events/${captured("default_no_benchmark").event_id}?bpage=2`);
    expect(within(paging).queryByRole("link", { name: "Previous benchmarks" })).toBeNull();
  });

  it("requests the page the address names, and keeps a selected benchmark that is not on it", async () => {
    const c = captured("benchmark_selected");
    serve({
      benchmarks: jsonResponse({ items: [CAPTURE.benchmarks.items[0]], total: 450, limit: 200, offset: 200 }),
      observation: () => jsonResponse(c.response),
    });
    await open("benchmark_selected", { ...c.params, bpage: "2" });
    expect(calls("/api/v1/benchmarks")[0]).toContain("offset=200");
    const select = screen.getByLabelText("Benchmark") as HTMLSelectElement;
    expect(select.value).toBe(BENCHMARK_ID);
    expect([...select.options].some((o) => /not on this page/.test(o.textContent ?? ""))).toBe(true);
    expect(screen.getByRole("link", { name: "Previous benchmarks" })).toBeInTheDocument();
  });
});

describe("the configuration form and the context bar stay honest", () => {
  it("applying keeps the benchmark list on the page being browsed (bpage travels with the form)", async () => {
    const c = captured("benchmark_selected");
    await open("benchmark_selected", { ...c.params, bpage: "2" });
    expect(document.querySelector('form input[type="hidden"][name="bpage"]')).toHaveValue("2");
    expect(document.querySelector('form input[name="bpage"]')).not.toBeNull();
  });

  it("no bpage field on the first page", async () => {
    await open("default_no_benchmark");
    expect(document.querySelector('form input[name="bpage"]')).toBeNull();
  });

  it("an invalid configuration is shown as typed and NOT applied: the bar never claims 'none' against the address", async () => {
    await open("default_no_benchmark", { horizons: "abc", benchmark_id: BENCHMARK_ID, filing_accession: "0000320193-24-000010", eps_kind: "DILUTED" });
    const bar = screen.getByRole("region", { name: "Research context" });
    expect(bar).toHaveTextContent(BENCHMARK_ID);
    expect(bar).toHaveTextContent("0000320193-24-000010");
    expect(bar).toHaveTextContent("abc");
    expect(bar.textContent!.match(/not applied/g)).toHaveLength(3);
    expect(bar).not.toHaveTextContent(/none \(stock returns only\)/);
    expect(bar).not.toHaveTextContent(/none \(not linked\)/);
  });
});

describe("horizons", () => {
  it("shows the default horizons as such", async () => {
    await open("default_no_benchmark");
    const bar = screen.getByRole("region", { name: "Research context" });
    expect(bar).toHaveTextContent("1, 5, 20");
    expect(bar).toHaveTextContent("default");
    expect(screen.getByLabelText(/Horizons/)).toHaveValue("");
  });

  it("shows custom horizons without the default tag, and keeps them in the field", async () => {
    await open("benchmark_selected", searchOf("benchmark_selected"));
    expect(screen.getByRole("region", { name: "Research context" })).toHaveTextContent(/Horizons\s*1, 2/);
    expect(screen.getByRole("region", { name: "Research context" })).not.toHaveTextContent("default");
    expect(screen.getByLabelText(/Horizons/)).toHaveValue("1,2");
  });

  it("offers presets as plain links that keep the rest of the configuration", async () => {
    await open("benchmark_selected", searchOf("benchmark_selected"));
    const id = captured("benchmark_selected").event_id;
    expect(screen.getByRole("link", { name: "Default (1,5,20)" })).toHaveAttribute("href", `/research/events/${id}?benchmark_id=${BENCHMARK_ID}`);
    expect(screen.getByRole("link", { name: "1,5" })).toHaveAttribute("href", `/research/events/${id}?horizons=1,5&benchmark_id=${BENCHMARK_ID}`);
  });

  it.each(["0", "1,1", "253", "1,2,3,4,5,6,7,8,9,10,11", "1, 5", "a"])("rejects %s locally: no observation request, the error is tied to the field", async (text) => {
    await open("default_no_benchmark", { horizons: text });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("This configuration cannot be used. No request was made.");
    expect(calls("/research/observations")).toHaveLength(0);
    const field = screen.getByLabelText(/Horizons/);
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAttribute("aria-describedby", alert.id);
    expect(field).toHaveValue(text);
    expect(descriptive).toThrow(); // no study, no stale one
  });
});

describe("descriptive and point-in-time are different modes", () => {
  it("a point-in-time refusal is a callout inside the PIT frame only and never dims the descriptive study", async () => {
    await open("benchmark_selected", searchOf("benchmark_selected"));
    const p = pit();
    expect(p).toHaveAttribute("data-mode", "POINT_IN_TIME");
    const notice = within(p).getByRole("status", { name: "Point-in-time study: Refused" });
    expect(notice).toHaveTextContent("INELIGIBLE_NO_AVAILABILITY_EVIDENCE");
    expect(notice).toHaveTextContent("This does not affect the descriptive study above.");
    expect(notice).toHaveTextContent("no approved availability evidence supports a point-in-time claim for this event");
    expect(within(p).queryByRole("alert")).toBeNull();
    expect(p.querySelector("svg[role=img]")).toBeNull(); // no empty chart pretending PIT was computed
    const d = descriptive();
    expect(d).toHaveAttribute("data-mode", "DESCRIPTIVE");
    expect(d.className).not.toMatch(/opacity|line-through|hidden/);
    expect(within(d).getByRole("table", { name: "Descriptive returns by horizon" })).toBeInTheDocument();
    expect(within(d).getByRole("img", { name: /Descriptive return by horizon/ })).toBeInTheDocument();
  });

  it("the two frames are visibly distinct: label, solid versus dashed rule, hue and a state badge", async () => {
    await open("benchmark_selected", searchOf("benchmark_selected"));
    expect(within(descriptive()).getByRole("heading", { level: 2 })).toHaveTextContent("DESCRIPTIVE");
    expect(within(pit()).getByRole("heading", { level: 2 })).toHaveTextContent("POINT-IN-TIME");
    // (Tailwind has no per-side border-style utilities: the rule's style is an arbitrary property.)
    expect(descriptive().className).toContain("[border-left-style:solid]");
    expect(pit().className).toContain("[border-left-style:dashed]");
    expect(pit().className).not.toContain("border-l-dashed");
    expect(descriptive().className).toContain("mode-descriptive");
    expect(pit().className).toContain("mode-pit");
    expect(descriptive()).toHaveTextContent("What happened after this event.");
    expect(pit()).toHaveTextContent("What could have been known and acted on, given the availability evidence.");
    expect(descriptive().contains(pit())).toBe(false);
  });

  it("an eligible event shows the point-in-time study through the same components", async () => {
    await open("pit_eligible", searchOf("pit_eligible"));
    const p = pit();
    expect(within(p).getByText("Evidence eligible")).toBeInTheDocument();
    expect(within(p).getByRole("table", { name: "Point-in-time returns by horizon" })).toBeInTheDocument();
    expect(within(p).getByRole("img", { name: /Point-in-time return by horizon/ })).toBeInTheDocument();
    expect(p).toHaveTextContent("Eligibility is an evidence-policy result, not a recommendation.");
    expect(within(descriptive()).getByRole("table", { name: "Descriptive returns by horizon" })).toBeInTheDocument();
  });

  it("uses no wording that claims tradability, safety or first publication", async () => {
    await open("pit_eligible", searchOf("pit_eligible"));
    const text = (screen.getByRole("region", { name: "POINT-IN-TIME" }).textContent ?? "") + (document.body.textContent ?? "");
    expect(text).not.toMatch(/safe to trade|tradable|tradeable|known to the market|guaranteed|first public(?! disclosure)/i);
  });

  it("the status strip names the modes' states and its refusal count equals the ledger", async () => {
    await open("benchmark_selected", searchOf("benchmark_selected"));
    const strip = screen.getByRole("navigation", { name: "Result status" });
    expect(within(strip).getByRole("link", { name: /Descriptive/ })).toHaveTextContent("Available");
    expect(within(strip).getByRole("link", { name: /Point-in-time/ })).toHaveTextContent("Refused");
    expect(within(strip).getByRole("link", { name: /Accounting/ })).toHaveTextContent("Not requested");
    const count = within(strip).getByTestId("refusal-count").textContent;
    expect(count).toBe("1");
    const ledger = document.getElementById("refusal-ledger")!;
    expect(within(ledger).getAllByRole("row").length - 1).toBe(Number(count));
  });
});

describe("refusals", () => {
  it("market refusal: an unknown timing replaces the ladder and the chart with the backend's reason", async () => {
    await open("unknown_timing", searchOf("unknown_timing"));
    const d = descriptive();
    expect(within(d).getByRole("status", { name: "Session alignment: Refused" })).toHaveTextContent("source declared no timing; before- vs after-market cannot be inferred");
    expect(d.querySelector("svg[role=img]")).toBeNull();
    expect(within(d).getByRole("note", { name: "No chart" })).toBeInTheDocument();
    expect(within(d).getAllByText("Unavailable").length).toBeGreaterThan(0); // the section's own state, and the horizons'
    // the table still lists each horizon's status
    expect(within(within(d).getByRole("table", { name: "Descriptive returns by horizon" })).getAllByText(/No alignment/)).toHaveLength(2);
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it("event off the stored calendar: missing sessions are MISSING, not a refusal and not an error", async () => {
    await open("event_off_calendar", searchOf("event_off_calendar"));
    expect(within(descriptive()).getByRole("status", { name: /Session alignment: Missing/ })).toBeInTheDocument();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it("event refusal: a missing reported EPS is NOT_STATED, listed under EVENT, and not an error", async () => {
    await open("missing_reported_eps", searchOf("missing_reported_eps"));
    const eventFrame = screen.getByRole("region", { name: "Event" });
    expect(within(eventFrame).getAllByText(/MISSING_REPORTED_EPS|Not stated/).length).toBeGreaterThan(0);
    const ledger = screen.getByRole("table", { name: /Event · 1 refusal/ });
    expect(within(ledger).getByText("MISSING_REPORTED_EPS")).toBeInTheDocument();
    expect(within(ledger).getByText("reported_eps is not yet available")).toBeInTheDocument();
  });

  it("the ledger groups by scope in the backend's scope order, with code, state, horizon and reason verbatim", async () => {
    await open("default_no_benchmark");
    const ledger = document.getElementById("refusal-ledger")!;
    const captions = [...ledger.querySelectorAll("caption")].map((c) => c.textContent);
    expect(captions).toEqual(["Market · 2 refusals", "Point-in-time · 1 refusal"]);
    const market = within(ledger).getByRole("table", { name: /Market/ });
    expect(within(market).getAllByText("INSUFFICIENT_FORWARD_SESSIONS")).toHaveLength(2);
    expect(within(market).getByText("fewer than 5 available trading sessions exist after the anchor session 2026-03-02")).toBeInTheDocument();
    expect(within(market).getByText("+20 sessions")).toBeInTheDocument();
    expect(ledger.querySelector('[data-state="MISSING"]')).not.toBeNull();
    expect(ledger.querySelector('[role="alert"]')).toBeNull();
  });

  it("a section's pointer links to a ledger group that exists (the first of its scopes that has entries)", async () => {
    await open("benchmark_without_data", searchOf("benchmark_without_data"));
    // This study has BENCHMARK refusals but no MARKET ones: the pointer must not aim at refusals-market.
    const link = within(descriptive()).getByRole("link", { name: "See the refusal ledger" });
    expect(link).toHaveAttribute("href", "#refusals-benchmark");
    expect(document.getElementById("refusals-benchmark")).not.toBeNull();
    expect(document.getElementById("refusals-market")).toBeNull();
  });

  it("does not repeat full refusal paragraphs in the sections: they point at the ledger", async () => {
    await open("default_no_benchmark");
    const reason = "fewer than 5 available trading sessions exist after the anchor session 2026-03-02";
    expect(screen.getAllByText(reason).length).toBeGreaterThanOrEqual(1);
    const outsideLedger = [...document.querySelectorAll("*")].filter((el) => el.children.length === 0 && el.textContent === reason && !el.closest("#refusal-ledger") && !el.hasAttribute("title"));
    // the table's cell tooltip carries it, the visible section text only references the ledger
    expect(outsideLedger).toHaveLength(0);
    expect(within(descriptive()).getByRole("link", { name: "See the refusal ledger" })).toHaveAttribute("href", "#refusals-market");
    expect(document.getElementById("refusals-market")).not.toBeNull();
  });

  it("an empty ledger says that every requested part was computed", async () => {
    await open("pit_eligible", searchOf("pit_eligible"));
    expect(screen.getByText("No refusals: every part that was requested was computed.")).toBeInTheDocument();
    expect(screen.getByTestId("refusal-count")).toHaveTextContent("0");
  });
});

describe("accounting and filing: only a filing the reader supplied", () => {
  const section = () => document.getElementById("section-accounting")!;

  it("no filing: NOT_REQUESTED with a link to supply one, and no disclosure level above NONE", async () => {
    await open("default_no_benchmark");
    expect(within(section()).getByRole("status", { name: "Accounting: Not requested" })).toHaveTextContent("A filing is never chosen for you");
    expect(section()).toHaveTextContent("No filing evidence is linked to this study.");
    expect(within(section()).getByText("NOT_LINKED")).toBeInTheDocument();
    expect(calls("/research/observations")[0]).not.toMatch(/filing_accession/);
  });

  it("an explicit filing is sent with both selectors, and the evaluated EPS is shown exactly", async () => {
    const c = captured("filing_evaluated");
    await open("filing_evaluated", c.params);
    const url = new URL(calls("/research/observations")[0]!);
    expect(url.searchParams.get("filing_accession")).toBe(c.params.filing_accession);
    expect(url.searchParams.get("eps_period_class")).toBe("QUARTER");
    expect(url.searchParams.get("eps_kind")).toBe("DILUTED");
    expect(within(section()).getByRole("region", { name: "Current EPS" })).toHaveTextContent("1.5300");
    const comparative = within(section()).getByRole("region", { name: "Comparative EPS" });
    expect(comparative).toHaveTextContent("As presented in this filing");
    expect(comparative).toHaveTextContent("1.5200");
    expect(within(section()).getByRole("region", { name: "EPS change" })).toHaveTextContent("+0.0100");
  });

  it("keeps PERIOD_UNVERIFIED exactly that and never says the period was verified", async () => {
    await open("filing_evaluated", captured("filing_evaluated").params);
    expect(within(section()).getByText("PERIOD_UNVERIFIED")).toBeInTheDocument();
    expect(section()).toHaveTextContent("The filing's fiscal period was not checked against this event");
    expect(section().textContent).not.toMatch(/period (was )?verified|verified period|matches this event/i);
    expect(within(section()).getByText("CALLER_SUPPLIED_CIK_CONSISTENT")).toBeInTheDocument();
    expect(section()).toHaveTextContent("Supplied by you; registrant consistent");
  });

  it.each([
    ["filing_not_stored", "FILING_NOT_STORED", "MISSING", "Filing not stored"],
    ["filing_cik_mismatch", "CIK_MISMATCH_REFUSED", "REFUSED", "Registrant (CIK) mismatch"],
  ])("%s keeps its own link state and refuses the accounting without borrowing anything", async (name, code, state, label) => {
    await open(name, captured(name).params);
    const frame = within(section()).getByRole("region", { name: "Filing link and disclosure evidence" });
    expect(within(frame).getByText(code)).toBeInTheDocument();
    expect(frame.querySelector(`[data-state="${state}"]`)).not.toBeNull();
    expect(frame).toHaveTextContent(label);
    expect(within(section()).getByRole("status", { name: "Accounting: Refused" })).toHaveTextContent(`The filing link was refused (${code})`);
    expect(within(section()).queryByRole("region", { name: "Current EPS" })).toBeNull();
    expect(within(frame).getByText("None")).toBeInTheDocument(); // disclosure evidence level NONE
    expect(descriptive()).toBeInTheDocument(); // the descriptive study is untouched
  });

  it("an accounting refusal keeps the exact status, reason and the distinct refusal statuses", async () => {
    await open("filing_comparative_missing", captured("filing_comparative_missing").params);
    const comparative = within(section()).getByRole("region", { name: "Comparative EPS" });
    expect(comparative).toHaveTextContent("COMPARATIVE_MISSING");
    expect(comparative.querySelector('[data-state="MISSING"]')).not.toBeNull();
    expect(within(section()).getByRole("region", { name: "EPS change" })).toHaveTextContent("INPUT_UNAVAILABLE");
    expect(within(section()).getByRole("region", { name: "EPS change" }).textContent).not.toMatch(/%/);
    await open("filing_refused_selector", captured("filing_refused_selector").params);
  });

  it("an unsupported form/period selection is a refusal of the accounting, not an error", async () => {
    await open("filing_refused_selector", captured("filing_refused_selector").params);
    const current = within(document.getElementById("section-accounting")!).getByRole("region", { name: "Current EPS" });
    expect(current).toHaveTextContent("UNSUPPORTED_FORM_PERIOD");
    expect(current.querySelector('[data-state="REFUSED"]')).not.toBeNull();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it.each([
    ["filing_evaluated", "D0_FILING_EXISTS", "A stored filing with this accession exists."],
    ["filing_d0_plain_8k", "D0_FILING_EXISTS", "A stored filing with this accession exists."],
    ["filing_d1", "D1_ITEM_2_02_DECLARED", "declares Item 2.02"],
  ])("%s: disclosure evidence reads conservatively", async (name, level, sentence) => {
    await open(name, captured(name).params);
    const text = document.getElementById("section-accounting")!.textContent ?? "";
    expect(text).toContain(sentence);
    expect(text).toContain("It implies no time, no first public disclosure and no availability");
    expect(text).not.toMatch(/attached|press release|release time|disclosed at|available to the market|made public/i);
    expect(document.body.textContent).not.toMatch(level.replace(/_/g, " ").toLowerCase() + " at");
    if (name === "filing_d1") expect(text).toContain("Stored 8-K items: 2.02,9.01");
  });

  it("shows no disclosure timestamp: none is supplied, and accepted_at, filing_date and observed_at are not reused", async () => {
    await open("filing_d1", captured("filing_d1").params);
    const frame = within(section()).getByRole("region", { name: "Filing link and disclosure evidence" });
    expect(frame.textContent).not.toMatch(/(?<![\d-])\d{4}-\d{2}-\d{2}(?!\d)/); // no calendar date (the accession is not one)
    expect(frame.textContent).not.toMatch(/UTC/);
  });

  it("a D1 filing does not upgrade the point-in-time verdict", async () => {
    await open("filing_d1", captured("filing_d1").params);
    expect(within(pit()).getAllByText("Refused").length).toBeGreaterThan(0);
  });

  it("an incomplete filing configuration is rejected locally with no request", async () => {
    const c = captured("filing_evaluated");
    await open("filing_evaluated", { filing_accession: c.params.filing_accession! });
    expect(screen.getByRole("alert")).toHaveTextContent("A filing needs both a period class and an EPS kind");
    expect(calls("/research/observations")).toHaveLength(0);
  });
});

describe("provenance and evidence", () => {
  it("exposes distinct kinds of evidence, each behind its own trigger", async () => {
    await open("filing_evaluated", captured("filing_evaluated").params);
    for (const subject of ["the stored event", "the stock returns", "the benchmark", "the filing", "the accounting facts"]) {
      expect(screen.getAllByRole("button", { name: `Evidence for ${subject}` }).length, subject).toBeGreaterThan(0);
    }
  });

  it("states that a live study has no reproducibility level", async () => {
    await open("default_no_benchmark");
    expect(screen.getByText("Reproducibility is assessed when a study is saved as a baseline. A live study shows no level.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\b(COMPLETE|PARTIAL)\b(?! \()/);
  });
});

describe("the research context bar", () => {
  it("shows identity from the stored event record only, and the configuration read back from the address", async () => {
    await open("benchmark_selected", searchOf("benchmark_selected"));
    const bar = screen.getByRole("region", { name: "Research context" });
    expect(bar).toHaveTextContent("Observation Co");
    expect(bar).toHaveTextContent("2026-03-02 · FY2026 Q1 · Before open");
    expect(bar).toHaveTextContent("Split-adjusted");
    expect(bar).toHaveTextContent(/Filing\s*none \(not linked\)/);
    expect(within(bar).getByRole("link", { name: "Stored event record" })).toHaveAttribute("href", `/earnings/${captured("benchmark_selected").event_id}`);
    expect(within(bar).getByRole("link", { name: "Remove the benchmark" })).toHaveAttribute("href", `/research/events/${captured("benchmark_selected").event_id}?horizons=1,2`);
  });

  it("shows a selected filing as a chip with its selectors and a way to remove it", async () => {
    const c = captured("filing_evaluated");
    await open("filing_evaluated", c.params);
    const bar = screen.getByRole("region", { name: "Research context" });
    expect(bar).toHaveTextContent(c.params.filing_accession!);
    expect(bar).toHaveTextContent("quarter · diluted");
    expect(within(bar).getByRole("link", { name: "Remove the filing" }).getAttribute("href")).not.toMatch(/filing_accession|eps_/);
  });
});

describe("filing discovery decision: an explicit accession, never a picker", () => {
  it("offers an accession field, a lookup link that claims no relationship, and no candidate list", async () => {
    await open("default_no_benchmark");
    const field = screen.getByLabelText("Accession");
    expect(field).toHaveValue("");
    expect(screen.getByLabelText("Period class")).toHaveValue("");
    expect(screen.getByLabelText("EPS kind")).toHaveValue("");
    const lookup = screen.getByRole("link", { name: /Filings for this registrant \(CIK 0000320193\)/ });
    expect(lookup).toHaveAttribute("href", "/filings?cik=0000320193");
    expect(screen.getByText(/does not say a filing belongs to this event/)).toBeInTheDocument();
    expect(calls("/api/v1/filings")).toHaveLength(0);
    expect(document.body.textContent).not.toMatch(/Earnings filing|Related filing|nearest|latest 10-Q|latest 10-K/i);
  });
});

describe("failures of the observation read are errors; refusals are not", () => {
  const each = async (reply: Reply, expectedTitle: RegExp) => {
    serve({ observation: () => reply });
    await open("benchmark_selected", searchOf("benchmark_selected"));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(expectedTitle);
    expect(alert.textContent).not.toMatch(/localhost|127\.0\.0\.1|:8000|http/);
    // the page, the context and the configuration survive
    expect(screen.getByRole("region", { name: "Research context" })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Research configuration" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "DESCRIPTIVE" })).toBeNull();
  };

  it("503", () => each(jsonResponse({ detail: "Database unavailable." }, 503), /Temporarily unavailable/));
  it("500", () => each(jsonResponse({ detail: "boom" }, 500), /unexpected error/));
  it("network failure", () => each(new TypeError("connect ECONNREFUSED 127.0.0.1:8000"), /Cannot reach the QuantForge API/));
  it("a rejection from the API shows its message about the parameters", () => each(jsonResponse(CAPTURE.rejected_horizons.body, 422), /The API rejected these parameters/));

  it("contract failure shows a safe contract error and none of the payload", async () => {
    const broken = clone(captured("benchmark_selected").response) as unknown as Json;
    broken.descriptive.study.stock_returns.forward_returns[0].return_value = 0.1;
    await each(jsonResponse(broken), /does not understand/);
    expect(document.body.textContent).not.toMatch(/1\.2000/);
  });

  it("a response for another benchmark is refused as a contract failure, not shown", async () => {
    const other = clone(captured("benchmark_selected").response) as unknown as Json;
    other.parameters.benchmark_id = EMPTY_BENCHMARK_ID;
    await each(jsonResponse(other), /does not understand/);
  });
});

describe("unknown events and malformed addresses", () => {
  it("a malformed event id is not found, and nothing is requested", async () => {
    await expect(ResearchEventPage({ params: Promise.resolve({ eventId: "nope" }), searchParams: Promise.resolve({}) } as never)).rejects.toThrow("NEXT_NOT_FOUND");
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("a well-formed unknown event is not found, and no observation is requested", async () => {
    await expect(
      ResearchEventPage({ params: Promise.resolve({ eventId: "99999999-9999-4999-8999-999999999999" }), searchParams: Promise.resolve({}) } as never),
    ).rejects.toThrow("NEXT_NOT_FOUND");
    expect(calls("/research/observations")).toHaveLength(0);
  });

  it("an event record that cannot be read is a page-level error with a way back", async () => {
    serve({ record: new TypeError("down") });
    const c = captured("default_no_benchmark");
    await renderServer(await ResearchEventPage({ params: Promise.resolve({ eventId: c.event_id }), searchParams: Promise.resolve({}) } as never));
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach the QuantForge API");
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeInTheDocument();
    expect(calls("/research/observations")).toHaveLength(0);
  });
});

describe("request architecture", () => {
  it("makes exactly ONE observation request (not one per mode, horizon, refusal or section) and one record read", async () => {
    await open("filing_evaluated", captured("filing_evaluated").params);
    expect(calls("/research/observations")).toHaveLength(1);
    expect(calls("/api/v1/earnings-events/")).toHaveLength(1);
    const kinds = new Set(vi.mocked(fetch).mock.calls.map((c) => new URL(String(c[0])).pathname.replace(/[0-9a-f-]{36}/, ":id")));
    expect(kinds).toEqual(new Set(["/api/v1/benchmarks", "/api/v1/research/observations/:id", "/api/v1/earnings-events/:id"]));
    // never a filing list, a per-horizon call or an enrichment crawl
    expect(calls("/api/v1/filings")).toHaveLength(0);
  });

  it("issues only GET requests", async () => {
    await open("benchmark_selected", searchOf("benchmark_selected"));
    for (const [, init] of vi.mocked(fetch).mock.calls) expect((init as RequestInit).method).toBe("GET");
  });

  it("an invalid configuration reads the record and the benchmark list but never the observation", async () => {
    await open("default_no_benchmark", { horizons: "0" });
    expect(calls("/research/observations")).toHaveLength(0);
  });
});

describe("page structure", () => {
  it("one h1, sections as h2, breadcrumbs and a labelled configuration form", async () => {
    await open("benchmark_selected", searchOf("benchmark_selected"));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const h2 = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(h2).toEqual(["Event", "DESCRIPTIVE", "POINT-IN-TIME", "Accounting and filing", "Filing link and disclosure evidence", "Refusal ledger", "Provenance"].filter((t) => h2.includes(t)));
    expect(h2).toEqual(expect.arrayContaining(["Event", "DESCRIPTIVE", "POINT-IN-TIME", "Accounting and filing", "Refusal ledger", "Provenance"]));
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Research" })).toHaveAttribute("href", "/research");
    const form = screen.getByRole("form", { name: "Research configuration" });
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", `/research/events/${captured("benchmark_selected").event_id}`);
    expect(within(form).getByRole("button", { name: "Apply" })).toBeInTheDocument();
    expect(within(form).getByRole("link", { name: "Reset" })).toHaveAttribute("href", `/research/events/${captured("benchmark_selected").event_id}`);
  });

  it("uses no trading or predictive language anywhere on the page", async () => {
    await open("pit_eligible", searchOf("pit_eligible"));
    // The backend's own method note (in a disclosure) says what the figures are NOT ("not a CAPM alpha").
    const body = document.body.cloneNode(true) as HTMLElement;
    body.querySelectorAll("details").forEach((el) => el.remove());
    const text = (body.textContent ?? "").replace(/Not a signal, forecast or recommendation\./g, "").replace(/Eligibility is an evidence-policy result, not a recommendation\./g, "");
    expect(text).not.toMatch(/\b(buy|sell|bullish|bearish|alpha|score|confidence|rank(ing)?|predict(ion|ive)?|outperform|underperform)\b/i);
  });

  it("makes no Saved Research claim and shows no verification UI", async () => {
    await open("benchmark_selected", searchOf("benchmark_selected"));
    expect(document.body.textContent).not.toMatch(/MATCH|Reproduced|Save this|verify/i);
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
  });
});
