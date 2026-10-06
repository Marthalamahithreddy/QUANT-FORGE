/**
 * Earnings list: URL parsing and small display helpers.
 *
 * There is no sorting and no filtering here. Order (newest event date first) and every filter are
 * the API's; this module only turns a URL into the three server-supported filters, strictly.
 */

import { isCalendarDate } from "@/lib/calendar";
import type { EarningsQuery } from "@/lib/api/earnings";
import type { EarningsEvent } from "@/lib/api/schema";
import {
  canonicalHref,
  isCanonical,
  offsetOfPage,
  parsePage,
  readParam,
  type ListError,
  type RawSearchParams,
} from "@/lib/list-query";

export const EARNINGS_PATH = "/earnings";

const TICKER_MAX = 32;

/**
 * Tickers the backend PROVED for this company on the event date (its historical resolver). Empty
 * when nothing is proven, which the UI states as "Not resolved" instead of guessing from a
 * listing that was valid at some other time or from a current ticker.
 */
export function tickersAtEvent(event: Pick<EarningsEvent, "resolved_tickers">): readonly string[] {
  return event.resolved_tickers;
}

/** `FY2026 Q1`, or `FY2026` when the source states no quarter. */
export function fiscalPeriod(event: Pick<EarningsEvent, "fiscal_year" | "fiscal_quarter">): string {
  return event.fiscal_quarter === null
    ? `FY${event.fiscal_year}`
    : `FY${event.fiscal_year} Q${event.fiscal_quarter}`;
}

export interface EarningsListState {
  /** What the reader typed (for the form), valid or not. */
  readonly shown: { readonly ticker: string; readonly date_from: string; readonly date_to: string };
  /** The request, built only from VALID values. */
  readonly query: EarningsQuery;
  readonly page: number;
  readonly errors: readonly ListError[];
  readonly filtered: boolean;
  /** The canonical address of this view; `null` when the URL is invalid. */
  readonly canonical: string | null;
  /** True when the URL is valid but not canonical (empty params, `page=1`, unknown params). */
  readonly redirect: boolean;
}

function hrefFor(query: EarningsQuery, page: number, path: string = EARNINGS_PATH): string {
  return canonicalHref(
    path,
    [
      ["ticker", query.ticker],
      ["date_from", query.dateFrom],
      ["date_to", query.dateTo],
    ],
    page,
  );
}

/** The URL of one page of a filtered earnings list. */
export const earningsPageHref = hrefFor;

export function parseEarningsList(raw: RawSearchParams, path: string = EARNINGS_PATH): EarningsListState {
  const ticker = readParam(raw, "ticker");
  const dateFrom = readParam(raw, "date_from");
  const dateTo = readParam(raw, "date_to");
  const { page, error: pageError } = parsePage(raw);
  const errors: ListError[] = [];

  const tickerText = ticker.text.trim();
  if (ticker.repeated) errors.push({ param: "ticker", message: "Ticker must be a single value." });
  else if (tickerText.length > TICKER_MAX) {
    errors.push({ param: "ticker", message: `Ticker must be at most ${TICKER_MAX} characters.` });
  }

  for (const [param, value, label] of [
    ["date_from", dateFrom, "From date"],
    ["date_to", dateTo, "To date"],
  ] as const) {
    if (value.repeated) errors.push({ param, message: `${label} must be a single value.` });
    else if (value.text !== "" && !isCalendarDate(value.text)) {
      errors.push({ param, message: `${label} must be a real date written YYYY-MM-DD.` });
    }
  }
  if (
    errors.every((error) => error.param !== "date_from" && error.param !== "date_to") &&
    dateFrom.text !== "" &&
    dateTo.text !== "" &&
    dateFrom.text > dateTo.text
  ) {
    errors.push({ param: "date_from", message: "From date is after the To date." });
  }
  if (pageError) errors.push(pageError);

  const query: EarningsQuery = {
    ticker: !ticker.repeated && tickerText.length <= TICKER_MAX ? tickerText || undefined : undefined,
    dateFrom: errors.some((e) => e.param === "date_from") ? undefined : dateFrom.text || undefined,
    dateTo: errors.some((e) => e.param === "date_to") ? undefined : dateTo.text || undefined,
    offset: offsetOfPage(page),
  };
  const canonical = errors.length === 0 ? hrefFor(query, page, path) : null;

  return {
    shown: { ticker: ticker.text, date_from: dateFrom.text, date_to: dateTo.text },
    query,
    page,
    errors,
    filtered: Boolean(query.ticker || query.dateFrom || query.dateTo),
    canonical,
    redirect: canonical !== null && !isCanonical(raw, canonical),
  };
}
