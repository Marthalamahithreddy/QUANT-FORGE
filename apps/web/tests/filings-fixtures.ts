import type { FilingIndexItem } from "@/lib/api/schema";

/** Test-only builders. Production code never contains sample data. */
export function makeFiling(
  overrides: Partial<FilingIndexItem> = {},
): FilingIndexItem {
  return {
    accession: "0000320193-24-000010",
    company_cik: "0000320193",
    form: "10-Q",
    filing_date: "2024-05-03",
    report_date: "2024-03-31",
    fsds_period_end: "2024-03-31",
    ...overrides,
  };
}

export function filingsPage(
  items: unknown[],
  total = items.length,
  offset = 0,
  limit = 50,
) {
  return { items, total, limit, offset };
}

type Json = Record<string, unknown>;

export function makeFact(overrides: Json = {}): Json {
  return {
    fact_id: "33333333-3333-4333-8333-333333333333",
    tag: "EarningsPerShareDiluted",
    taxonomy_version: "us-gaap/2024",
    unit: "USD",
    period_end: "2024-03-31",
    qtrs: 1,
    value: "1.5300",
    segments: "",
    coreg: "",
    ...overrides,
  };
}

/** A valid AVAILABLE chain: current 1.5300, comparative 1.0200, +0.5100 (a 50% ratio). */
export function makeEps(overrides: Json = {}): Json {
  return {
    accession: "0000320193-24-000010",
    company_cik: "0000320193",
    form: "10-Q",
    period_class: "QUARTER",
    eps_kind: "DILUTED",
    descriptive_only: true,
    no_point_in_time_claim: true,
    boundary_note: "Descriptive accounting data from SEC FSDS.",
    current: {
      status: "AVAILABLE",
      reason: "A single standard fact was selected.",
      fact: makeFact(),
    },
    comparative: {
      status: "AVAILABLE",
      reason: "The comparative fact was identified.",
      meaning: "comparative EPS as presented in this filing",
      fact: makeFact({
        fact_id: "44444444-4444-4444-8444-444444444444",
        period_end: "2023-03-31",
        value: "1.0200",
      }),
    },
    change: {
      status: "AVAILABLE",
      eps_change: "0.5100",
      transition: "POSITIVE_INCREASED",
      percentage_change: "0.5",
      percentage_semantics: "ratio",
      percentage_unavailable_reason: null,
    },
    ...overrides,
  };
}

export const UNAVAILABLE_CHANGE = {
  status: "INPUT_UNAVAILABLE",
  eps_change: null,
  transition: null,
  percentage_change: null,
  percentage_semantics: "ratio",
  percentage_unavailable_reason: null,
};

export const REFUSED_CURRENT = (
  status: string,
  reason = "Refused with a stated reason.",
) => ({
  status,
  reason,
  fact: null,
});
