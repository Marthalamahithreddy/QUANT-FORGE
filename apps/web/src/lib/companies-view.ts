/**
 * Presentation helpers for the company pages: URL state, labels and exact money display.
 *
 * Nothing here computes a financial figure. `inMillions` moves a decimal point in an exact string
 * (no number is ever built from a financial value); the exact string stays available to the reader.
 */

import { canonicalHref, isCanonical, offsetOfPage, parsePage, readParam, type ListError, type RawSearchParams } from "@/lib/list-query";
import type { CompaniesQuery } from "@/lib/api/companies";
import type { FilingFinancials, MetricId, Transition } from "@/lib/api/schema";

export const COMPANIES_PATH = "/companies";
export const MAX_QUERY_LENGTH = 80;

export const companyHref = (cik: string): string => `${COMPANIES_PATH}/${encodeURIComponent(cik)}`;

/**
 * Concept-specific names: each metric is ONE exact us-gaap concept, not a comprehensive measure for
 * every industry. Revenue is `RevenueFromContractWithCustomerExcludingAssessedTax` (revenue from
 * customer contracts, which for a bank or insurer can be a subset of total revenue), and net income
 * is `NetIncomeLoss` (attributable to the parent, before any noncontrolling interest).
 */
export const METRIC_LABEL: Record<MetricId, string> = {
  REVENUE: "Revenue (customer contracts)",
  NET_INCOME: "Net income (parent)",
  EPS_DILUTED: "Diluted EPS",
};

/** `usd` figures are shown in USD millions; `eps` is USD per share exactly as stored. */
export const METRIC_KIND: Record<MetricId, "usd" | "eps"> = {
  REVENUE: "usd",
  NET_INCOME: "usd",
  EPS_DILUTED: "eps",
};

/** The unit every figure of a metric is shown in (USD figures are moved to millions for display). */
export const METRIC_UNIT: Record<MetricId, string> = {
  REVENUE: "USD millions",
  NET_INCOME: "USD millions",
  EPS_DILUTED: "USD per share",
};

/** Neutral wording: the same transitions describe revenue, income and per-share figures. */
export const CHANGE_LABEL: Record<Transition, string> = {
  POSITIVE_INCREASED: "Increased",
  POSITIVE_DECREASED: "Decreased",
  POSITIVE_UNCHANGED: "Unchanged",
  LOSS_NARROWED: "Loss narrowed",
  LOSS_WIDENED: "Loss widened",
  LOSS_UNCHANGED: "Loss unchanged",
  LOSS_TO_PROFIT: "Loss to profit",
  PROFIT_TO_LOSS: "Profit to loss",
  ZERO_TO_PROFIT: "Zero to profit",
  ZERO_TO_LOSS: "Zero to loss",
  PROFIT_TO_ZERO: "Profit to zero",
  LOSS_TO_ZERO: "Loss to zero",
  ZERO_UNCHANGED: "Remained zero",
};

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/**
 * An exact decimal string moved six places (USD to USD millions), trailing zeros trimmed. Lossless:
 * `"11279000000.0000"` becomes `"11279"`, `"10000000.0000"` becomes `"10"`, `"1234567"` becomes `"1.234567"`.
 */
export function inMillions(exact: string): string | null {
  const match = DECIMAL.exec(exact);
  if (match === null) return null;
  const [, sign = "", whole = "", fraction = ""] = match;
  const digits = `${whole}${fraction}`;
  const point = whole.length - 6;
  const padded = point > 0 ? digits : `${"0".repeat(1 - point)}${digits}`;
  const cut = point > 0 ? point : 1;
  const integer = padded.slice(0, cut).replace(/^0+(?=\d)/, "");
  const rest = padded.slice(cut).replace(/0+$/, "");
  const text = rest === "" ? integer : `${integer}.${rest}`;
  return text === "0" ? "0" : `${sign}${text}`;
}

/**
 * `Q3 FY2026` for a quarter, `FY2026` for a year, else the period end date. A label is built only
 * from a fiscal year plus a fiscal period consistent with the period class (a quarter needs Q1-Q4;
 * a year needs FY or none), so a quarter without a stated period is never shown as "FY2026".
 */
export function fiscalLabel(filing: Pick<FilingFinancials, "fiscal_period" | "fiscal_year" | "period_end" | "period_class">): string {
  const period = filing.fiscal_period;
  const consistent = filing.period_class === "QUARTER" ? period !== null && /^Q[1-4]$/.test(period) : period === null || period === "FY";
  if (filing.fiscal_year !== null && consistent) {
    return filing.period_class === "QUARTER" ? `${period} FY${filing.fiscal_year}` : `FY${filing.fiscal_year}`;
  }
  return filing.period_end ?? "Period not stated";
}

/**
 * A quarter's label in the trend. An amendment names its form, so an original 10-Q and its 10-Q/A for
 * the same quarter are never two identical-looking bars.
 */
export function quarterLabel(filing: Parameters<typeof fiscalLabel>[0] & Pick<FilingFinancials, "form">): string {
  return filing.form === "10-Q" ? fiscalLabel(filing) : `${fiscalLabel(filing)} · ${filing.form}`;
}

export const PERIOD_CLASS_NOUN = { QUARTER: "quarter", ANNUAL: "fiscal year" } as const;

export interface CompaniesListState {
  readonly shown: string;
  readonly query: CompaniesQuery;
  readonly page: number;
  readonly errors: readonly ListError[];
  readonly filtered: boolean;
  readonly canonical: string | null;
  readonly redirect: boolean;
}

export function companiesPageHref(query: CompaniesQuery, page: number): string {
  return canonicalHref(COMPANIES_PATH, [["q", query.q]], page);
}

export function parseCompaniesList(raw: RawSearchParams): CompaniesListState {
  const q = readParam(raw, "q");
  const { page, error: pageError } = parsePage(raw);
  const errors: ListError[] = [];
  const text = q.text.trim();
  if (q.repeated || text.length > MAX_QUERY_LENGTH) {
    errors.push({ param: "q", message: `Search must be a single value of at most ${MAX_QUERY_LENGTH} characters.` });
  }
  if (pageError) errors.push(pageError);
  const valid = !errors.some((e) => e.param === "q");
  const query: CompaniesQuery = { q: valid && text !== "" ? text : undefined, offset: offsetOfPage(page) };
  const canonical = errors.length === 0 ? companiesPageHref(query, page) : null;
  return {
    shown: q.text,
    query,
    page,
    errors,
    filtered: Boolean(query.q),
    canonical,
    redirect: canonical !== null && !isCanonical(raw, canonical),
  };
}
