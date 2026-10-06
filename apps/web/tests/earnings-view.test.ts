import { describe, expect, it } from "vitest";

import { EARNINGS_PATH, earningsPageHref, fiscalPeriod, parseEarningsList, tickersAtEvent } from "@/lib/earnings-view";
import { MAX_PAGE } from "@/lib/list-query";
import { makeEvent } from "./fixtures";

describe("tickersAtEvent", () => {
  it("returns exactly what the backend proved for the event date", () => {
    expect(tickersAtEvent(makeEvent({ resolved_tickers: ["X", "Y"] }))).toEqual(["X", "Y"]);
    expect(tickersAtEvent(makeEvent({ resolved_tickers: [] }))).toEqual([]);
  });

  it("does not fall back to a listing that was valid on the date", () => {
    const event = makeEvent({ resolved_tickers: [] });
    expect(event.listings.some((l) => l.valid_on_event_date)).toBe(true);
    expect(tickersAtEvent(event)).toEqual([]);
  });
});

describe("fiscalPeriod", () => {
  it("shows the quarter only when the source states one", () => {
    expect(fiscalPeriod({ fiscal_year: 2026, fiscal_quarter: 1 })).toBe("FY2026 Q1");
    expect(fiscalPeriod({ fiscal_year: 2026, fiscal_quarter: null })).toBe("FY2026");
  });
});

describe("parseEarningsList", () => {
  it("accepts an empty query as page 1 with no filters, at the canonical address", () => {
    const state = parseEarningsList({});
    expect(state).toMatchObject({ page: 1, errors: [], filtered: false, canonical: EARNINGS_PATH, redirect: false });
    expect(state.query).toEqual({ ticker: undefined, dateFrom: undefined, dateTo: undefined, offset: 0 });
  });

  it("builds the request from the three server-supported filters only", () => {
    const state = parseEarningsList({ ticker: "ACME", date_from: "2024-01-01", date_to: "2024-03-31", page: "3", sort: "reported_eps", q: "x" });
    expect(state.query).toEqual({ ticker: "ACME", dateFrom: "2024-01-01", dateTo: "2024-03-31", offset: 100 });
    expect(state.filtered).toBe(true);
    // Unknown parameters are neither forwarded nor kept: the canonical address drops them.
    expect(state.canonical).toBe("/earnings?ticker=ACME&date_from=2024-01-01&date_to=2024-03-31&page=3");
    expect(state.redirect).toBe(true);
  });

  it("omits defaults from the canonical address and asks to redirect a non-canonical one", () => {
    const state = parseEarningsList({ ticker: "", date_from: "", date_to: "", page: "1" });
    expect(state.canonical).toBe("/earnings");
    expect(state.redirect).toBe(true);
    expect(parseEarningsList({ page: "2" }).redirect).toBe(false);
  });

  it("treats an empty page parameter as the default and redirects it away (like every other empty parameter)", () => {
    const state = parseEarningsList({ ticker: "AAPL", page: "" });
    expect(state.errors).toEqual([]);
    expect(state.canonical).toBe("/earnings?ticker=AAPL");
    expect(state.redirect).toBe(true);
    expect(parseEarningsList({ page: ["", ""] }).errors.map((e) => e.param)).toContain("page");
  });

  it("does not redirect an address that is already canonical, whatever the parameter order", () => {
    expect(parseEarningsList({ date_to: "2024-03-31", ticker: "ACME" }).redirect).toBe(false);
  });

  it("trims surrounding whitespace on the ticker and keeps its case", () => {
    const state = parseEarningsList({ ticker: "  aapl " });
    expect(state.query.ticker).toBe("aapl");
    expect(state.canonical).toBe("/earnings?ticker=aapl");
  });

  it.each([
    ["a ticker over 32 characters", { ticker: "X".repeat(33) }, "ticker"],
    ["a repeated ticker", { ticker: ["A", "B"] }, "ticker"],
    ["a non-date From", { date_from: "yesterday" }, "date_from"],
    ["an impossible From date", { date_from: "2024-02-30" }, "date_from"],
    ["a non-date To", { date_to: "2024-13-01" }, "date_to"],
    ["a repeated date", { date_from: ["2024-01-01", "2024-02-01"] }, "date_from"],
    ["From after To", { date_from: "2024-05-01", date_to: "2024-01-01" }, "date_from"],
    ["a non-numeric page", { page: "2abc" }, "page"],
    ["page zero", { page: "0" }, "page"],
    ["a negative page", { page: "-1" }, "page"],
    ["a page beyond the bound", { page: String(MAX_PAGE + 1) }, "page"],
    ["a repeated page", { page: ["1", "2"] }, "page"],
  ])("rejects %s locally, without a request address", (_name, raw, param) => {
    const state = parseEarningsList(raw);
    expect(state.errors.map((e) => e.param)).toContain(param);
    expect(state.canonical).toBeNull();
    expect(state.redirect).toBe(false);
  });

  it("keeps what the reader typed so the form can show it, valid or not", () => {
    expect(parseEarningsList({ date_from: "yesterday" }).shown.date_from).toBe("yesterday");
  });

  it("never lets an invalid value reach the request", () => {
    const state = parseEarningsList({ ticker: "OK", date_from: "nope", page: "x" });
    expect(state.query.dateFrom).toBeUndefined();
    expect(state.query.offset).toBe(0);
  });

  it("accepts the last page the offset bound allows", () => {
    const state = parseEarningsList({ page: String(MAX_PAGE) });
    expect(state.errors).toEqual([]);
    expect(state.query.offset).toBe((MAX_PAGE - 1) * 50);
    expect(state.query.offset).toBeLessThanOrEqual(1_000_000);
  });
});

describe("earningsPageHref", () => {
  it("keeps every filter in the page links and omits the default page", () => {
    expect(earningsPageHref({ ticker: "A B" }, 1)).toBe("/earnings?ticker=A+B");
    expect(earningsPageHref({ dateFrom: "2024-01-01" }, 4)).toBe("/earnings?date_from=2024-01-01&page=4");
    expect(earningsPageHref({}, 1)).toBe("/earnings");
  });
});
