import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import CompaniesPage from "@/app/companies/page";
import CompanyPage from "@/app/companies/[cik]/page";
import { decodeCompanyFinancials } from "@/lib/api/decoders/companies";
import { decode } from "@/lib/api/decode";
import { barPercents } from "@/lib/bar-widths";
import { fiscalLabel, inMillions, parseCompaniesList } from "@/lib/companies-view";
import { jsonResponse } from "./fixtures";
import { renderServer } from "./render-server";

vi.mock("next/navigation", () => ({
  usePathname: () => "/companies",
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
  redirect: (to: string) => {
    throw new Error(`REDIRECT ${to}`);
  },
}));

const CIK = "0000111111";
const ACC = "0000111111-26-000001";
type Json = Record<string, unknown>;

/** Test-only shape of the API response with synthetic numbers; production code has no sample data. */
const fact = (value: string, periodEnd: string, tag = "NetIncomeLoss"): Json => ({
  fact_id: "11111111-1111-4111-8111-111111111111",
  tag,
  taxonomy_version: "us-gaap/2025",
  unit: "USD",
  period_end: periodEnd,
  qtrs: 1,
  value,
});
const point = (value: string, periodEnd: string): Json => ({ status: "AVAILABLE", reason: "one fact", fact: fact(value, periodEnd) });
const refused = (status: string): Json => ({ status, reason: `test reason ${status}`, fact: null });
const noChange: Json = { status: "INPUT_UNAVAILABLE", absolute: null, transition: null, ratio: null, ratio_unavailable_reason: null };

function financials(metrics: Json[] = [], extra: Json[] = [], total?: number): Json {
  return {
    company: { cik: CIK, name: "Example Corp", tickers: ["EXM"] },
    descriptive_only: true,
    no_point_in_time_claim: true,
    boundary_note: "Descriptive accounting data. No point-in-time availability claim.",
    total_filings: total ?? (metrics.length === 0 ? 0 : 1 + extra.length),
    filings: metrics.length === 0 ? [] : [filing(metrics), ...extra],
  };
}
function filing(metrics: Json[], overrides: Json = {}): Json {
  return {
    accession: ACC,
    form: "10-Q",
    period_class: "QUARTER",
    period_end: "2026-02-28",
    fiscal_year: 2026,
    fiscal_period: "Q3",
    filing_date: "2026-04-01",
    report_date: "2026-02-28",
    package_stored_at: "2026-10-07T16:18:22Z",
    source_artifact: "2026q2",
    source_artifact_sha256: "a".repeat(64),
    source_url: `https://www.sec.gov/Archives/edgar/data/111111/000011111126000001/${ACC}-index.htm`,
    metrics,
    ...overrides,
  };
}
const income = (): Json => ({
  metric: "NET_INCOME",
  current: point("520000000.0000", "2026-02-28"),
  prior: point("794000000.0000", "2025-02-28"),
  change: { status: "AVAILABLE", absolute: "-274000000.0000", transition: "POSITIVE_DECREASED", ratio: "-0.345088", ratio_unavailable_reason: null },
});
const revenueRefused = (): Json => ({ metric: "REVENUE", current: refused("WRONG_UNIT"), prior: refused("CURRENT_UNAVAILABLE"), change: noChange });

const props = (cik: string) => ({ params: Promise.resolve({ cik }), searchParams: Promise.resolve({}) }) as never;
const stub = (reply: Response | Error) =>
  vi.stubGlobal("fetch", vi.fn(() => (reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply))));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("exact decimal helpers", () => {
  it("moves the decimal point of an exact string without rounding or a number", () => {
    expect(inMillions("11279000000.0000")).toBe("11279");
    expect(inMillions("10000000.0000")).toBe("10");
    expect(inMillions("-274000000.0000")).toBe("-274");
    expect(inMillions("1234567")).toBe("1.234567");
    expect(inMillions("999999")).toBe("0.999999");
    expect(inMillions("0.0000")).toBe("0");
    expect(inMillions("12abc")).toBeNull();
  });

  it("sizes bars from magnitudes in integer arithmetic", () => {
    expect(barPercents(["520000000.0000", "-794000000.0000"])).toEqual(["65", "100"]);
    expect(barPercents(["0", "0"])).toEqual(["0", "0"]);
    expect(barPercents(["x", "5"])).toEqual([null, "100"]);
  });

  it("canonicalises the directory search and rejects a repeated or oversized one", () => {
    expect(parseCompaniesList({ q: " nike " }).canonical).toBe("/companies?q=nike");
    expect(parseCompaniesList({ q: ["a", "b"] }).errors).toHaveLength(1);
    expect(parseCompaniesList({ q: "x".repeat(81) }).errors).toHaveLength(1);
  });
});

describe("fiscal label", () => {
  const base = { period_end: "2026-02-28", fiscal_year: 2026, fiscal_period: "Q3", period_class: "QUARTER" } as const;

  it("labels a quarter and a year from a consistent fiscal year and period", () => {
    expect(fiscalLabel(base)).toBe("Q3 FY2026");
    expect(fiscalLabel({ ...base, period_class: "ANNUAL", fiscal_period: "FY" })).toBe("FY2026");
    expect(fiscalLabel({ ...base, period_class: "ANNUAL", fiscal_period: null })).toBe("FY2026");
  });

  it("never shows a quarter as a fiscal year: a missing or inconsistent period falls back to the period end", () => {
    expect(fiscalLabel({ ...base, fiscal_period: null })).toBe("2026-02-28");
    expect(fiscalLabel({ ...base, fiscal_period: "FY" })).toBe("2026-02-28");
    expect(fiscalLabel({ ...base, period_class: "ANNUAL", fiscal_period: "Q3" })).toBe("2026-02-28");
    expect(fiscalLabel({ ...base, fiscal_year: null })).toBe("2026-02-28");
    expect(fiscalLabel({ ...base, fiscal_year: null, period_end: null })).toBe("Period not stated");
  });
});

describe("the financials contract", () => {
  it("accepts a valid response and refuses a fact attached to a refused figure", () => {
    expect(decode(decodeCompanyFinancials, financials([income(), revenueRefused()])).ok).toBe(true);
    const bad = financials([{ ...revenueRefused(), current: { status: "WRONG_UNIT", reason: "r", fact: fact("5", "2026-02-28") } }]);
    expect(decode(decodeCompanyFinancials, bad).ok).toBe(false);
  });

  it("refuses a total smaller than the filings listed", () => {
    expect(decode(decodeCompanyFinancials, financials([income()], [], 5)).ok).toBe(true);
    expect(decode(decodeCompanyFinancials, financials([income()], [filing([income()], { accession: "0000111111-25-000002" })], 1)).ok).toBe(false);
  });

  it("refuses a JSON number in place of an exact string, and a change with an unavailable input", () => {
    const number = financials([income()]);
    ((number.filings as Json[])[0]!.metrics as Json[])[0]!.current = { status: "AVAILABLE", reason: "r", fact: { ...fact("1", "2026-02-28"), value: 520000000 } };
    expect(decode(decodeCompanyFinancials, number).ok).toBe(false);
    const orphan = financials([{ ...income(), prior: refused("MISSING") }]);
    expect(decode(decodeCompanyFinancials, orphan).ok).toBe(false);
  });
});

describe("Company page", () => {
  it("shows exact figures, the change, refusals by name, and the evidence behind them", async () => {
    stub(jsonResponse(financials([income(), revenueRefused()])));
    await renderServer(await CompanyPage(props(CIK)));

    expect(screen.getByRole("heading", { level: 1, name: "Example Corp" })).toBeInTheDocument();
    const card = screen.getByRole("article", { name: "Net income (parent)" });
    expect(within(card).getAllByText(/520/).length).toBeGreaterThan(0);
    expect(card.querySelector('[data-exact="520000000.0000"]')).not.toBeNull();
    expect(card.querySelector('[data-exact="-274000000.0000"]')).not.toBeNull();
    // A refused figure is named, never a zero or a blank.
    const revenue = screen.getByRole("article", { name: "Revenue (customer contracts)" });
    expect(revenue.querySelector('[data-state="REFUSED"]')).not.toBeNull();
    expect(within(revenue).getByText(/WRONG_UNIT/)).toBeInTheDocument();
    // Evidence: the filing page, the SEC link, the package and the stored fact.
    expect(screen.getByRole("link", { name: ACC })).toHaveAttribute("href", expect.stringContaining(`/filings/${ACC}?cik=${CIK}`));
    expect(screen.getByRole("link", { name: /Open the filing index on sec.gov/ })).toHaveAttribute("href", expect.stringContaining("sec.gov/Archives"));
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    expect(screen.getByText(/only one filing/i)).toBeInTheDocument();
    expect(screen.queryByText(/acceptance/i)).toBeNull();
    // Concept-specific names and visible units, in the cards and in the comparison table headers.
    expect(within(card).getByText("USD millions")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: /per stored filing/ });
    for (const header of ["Revenue (customer contracts), USD millions", "Net income (parent), USD millions", "Diluted EPS, USD per share"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
  });

  it("plots the available stored quarters oldest first, lists an unavailable quarter, and keeps the annual row out of the trend", async () => {
    const older = filing([{ ...income(), current: point("100000000.0000", "2025-02-28") }, revenueRefused()], {
      accession: "0000111111-25-000002",
      period_end: "2025-02-28",
      fiscal_year: 2025,
      source_url: "https://www.sec.gov/Archives/edgar/data/111111/000011111125000002/0000111111-25-000002-index.htm",
    });
    const annual = filing([income()], {
      accession: "0000111111-25-000003",
      form: "10-K",
      period_class: "ANNUAL",
      period_end: "2025-05-31",
      fiscal_period: "FY",
      fiscal_year: 2025,
      source_url: "https://www.sec.gov/Archives/edgar/data/111111/000011111125000003/0000111111-25-000003-index.htm",
    });
    stub(jsonResponse(financials([income(), revenueRefused()], [older, annual])));
    await renderServer(await CompanyPage(props(CIK)));
    const trend = screen.getByRole("region", { name: "Net income (parent) by quarter" });
    const labels = within(trend).getAllByText(/FY2025|FY2026/).map((node) => node.textContent);
    expect(labels).toEqual(["Q3 FY2025 · ended 2025-02-28", "Q3 FY2026 · ended 2026-02-28"]);
    // The quarter whose revenue was refused is listed, never drawn as zero.
    expect(within(screen.getByRole("region", { name: "Revenue (customer contracts) by quarter" })).getByText(/Not drawn/)).toBeInTheDocument();
    expect(screen.getAllByRole("row").length).toBeGreaterThan(3);
    expect(screen.queryByText(/only one filing/i)).toBeNull();
  });

  it("names an amendment in the trend so it is never a second identical quarter", async () => {
    const amendment = filing([{ ...income(), current: point("530000000.0000", "2026-02-28") }], {
      accession: "0000111111-26-000009",
      form: "10-Q/A",
      filing_date: "2026-05-01",
      source_url: "https://www.sec.gov/Archives/edgar/data/111111/000011111126000009/0000111111-26-000009-index.htm",
    });
    stub(jsonResponse(financials([income(), revenueRefused()], [amendment])));
    await renderServer(await CompanyPage(props(CIK)));
    const trend = screen.getByRole("region", { name: "Net income (parent) by quarter" });
    const labels = within(trend).getAllByText(/FY2026/).map((node) => node.textContent);
    // The original first (earlier filing date), the amendment named; both remain visible.
    expect(labels).toEqual(["Q3 FY2026 · ended 2026-02-28", "Q3 FY2026 · 10-Q/A · ended 2026-02-28"]);
  });

  it("says when older stored filings are not shown, and does not when the list is complete", async () => {
    stub(jsonResponse(financials([income(), revenueRefused()], [], 45)));
    await renderServer(await CompanyPage(props(CIK)));
    expect(screen.getByText(/Newest 1 of 45 stored filings/)).toBeInTheDocument();
    expect(screen.getByText(/44 older stored filings are not shown/)).toBeInTheDocument();

    cleanup();
    stub(jsonResponse(financials([income(), revenueRefused()])));
    await renderServer(await CompanyPage(props(CIK)));
    expect(screen.queryByText(/not shown/)).toBeNull();
    expect(screen.queryByText(/Newest 1 of/)).toBeNull();
  });

  it("says plainly that no data is stored, and fetches nothing else", async () => {
    stub(jsonResponse(financials([])));
    await renderServer(await CompanyPage(props(CIK)));
    expect(screen.getByText("No SEC financial data is stored for this company")).toBeInTheDocument();
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });

  it("makes no request for a malformed CIK, maps 404 to not-found and an outage to an error state", async () => {
    stub(jsonResponse({}));
    await renderServer(await CompanyPage(props("123")));
    expect(screen.getByText("Malformed CIK")).toBeInTheDocument();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();

    stub(jsonResponse({ detail: "x" }, 404));
    await expect(CompanyPage(props(CIK))).rejects.toThrow("NEXT_NOT_FOUND");

    cleanup();
    stub(new TypeError("connection refused"));
    await renderServer(await CompanyPage(props(CIK)));
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});

describe("Companies page", () => {
  it("lists stored companies with links, and says when nothing matches", async () => {
    const page = (items: Json[]) => ({ items, total: items.length, limit: 50, offset: 0 });
    stub(jsonResponse(page([{ cik: CIK, name: "Example Corp", tickers: ["EXM"], stored_filings: 164, filings_with_financial_facts: 1 }])));
    await renderServer(await CompaniesPage({ searchParams: Promise.resolve({ q: "exa" }), params: Promise.resolve({}) } as never));
    expect(screen.getByRole("link", { name: "Example Corp" })).toHaveAttribute("href", `/companies/${CIK}`);
    expect(screen.getByText(/of 164 stored/)).toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[0]![0])).toContain("/api/v1/companies?q=exa");

    cleanup();
    stub(jsonResponse(page([])));
    await renderServer(await CompaniesPage({ searchParams: Promise.resolve({ q: "zzz" }), params: Promise.resolve({}) } as never));
    expect(screen.getByText("No stored company matches this search")).toBeInTheDocument();
  });
});
