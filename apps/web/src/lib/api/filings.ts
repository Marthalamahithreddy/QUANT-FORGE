/**
 * Read-only filings reads: the FSDS-backed filings index and one filing's descriptive EPS.
 *
 * Every function resolves to a discriminated result instead of throwing, so a page always renders
 * an explicit state. Unlike the earnings reads, an unavailable database (HTTP 503), a rejected
 * request (422) and an unsupported payload are separate states, so none of them can masquerade as
 * "no filings". Only GET is ever issued, and only against the QuantForge API.
 */

import { ApiError, apiGet, type ApiGetOptions } from "@/lib/api/client";
import { PAGE_SIZE } from "@/lib/api/earnings";
import { env } from "@/lib/config/env";
import {
  FilingsContractError,
  parseFilingEps,
  parseFilingIndexPage,
  type EpsKind,
  type EpsPeriodClass,
  type FilingEps,
  type FilingForm,
  type FilingIndexPage,
} from "@/types/filings";

export type FilingsResult<T> =
  | { readonly state: "ok"; readonly data: T }
  /** HTTP 503: the database is unavailable. Never "no data". */
  | { readonly state: "unavailable" }
  /** HTTP 422: the API rejected the request parameters. */
  | { readonly state: "rejected"; readonly detail: string }
  /** The payload does not match the contract this frontend supports. */
  | { readonly state: "contract"; readonly detail: string }
  | { readonly state: "error"; readonly detail: string };

export interface FilingsQuery {
  readonly form?: FilingForm;
  readonly cik?: string;
  readonly offset?: number;
  readonly limit?: number;
}

async function guard<T>(run: () => Promise<T>): Promise<FilingsResult<T>> {
  try {
    return { state: "ok", data: await run() };
  } catch (error) {
    if (error instanceof FilingsContractError) {
      return { state: "contract", detail: error.message };
    }
    if (error instanceof ApiError) {
      if (error.status === 503) return { state: "unavailable" };
      if (error.status === 422)
        return { state: "rejected", detail: error.message };
      return { state: "error", detail: error.message };
    }
    return {
      state: "error",
      detail:
        error instanceof Error
          ? error.message
          : "Unexpected error contacting the API.",
    };
  }
}

export function buildFilingsPath(query: FilingsQuery): string {
  const params = new URLSearchParams();
  if (query.form) params.set("form", query.form);
  if (query.cik) params.set("cik", query.cik);
  params.set("limit", String(query.limit ?? PAGE_SIZE));
  params.set("offset", String(query.offset ?? 0));
  return `${env.apiFilingsPath}?${params.toString()}`;
}

export function buildFilingEpsPath(
  accession: string,
  periodClass: EpsPeriodClass,
  epsKind: EpsKind,
  cik: string | null = null,
): string {
  const params = new URLSearchParams({
    period_class: periodClass,
    eps_kind: epsKind,
  });
  // Registrant qualification: sent verbatim (leading zeros intact) only when the caller has one.
  if (cik !== null) params.set("cik", cik);
  return `${env.apiFilingsPath}/${encodeURIComponent(accession)}/eps?${params.toString()}`;
}

export function getFilings(
  query: FilingsQuery = {},
  options: ApiGetOptions = {},
): Promise<FilingsResult<FilingIndexPage>> {
  return guard(async () =>
    parseFilingIndexPage(
      await apiGet<unknown>(buildFilingsPath(query), options),
    ),
  );
}

/**
 * Both selectors are required and typed: nothing is defaulted or inferred here. `cik` optionally
 * qualifies WHICH stored filing `(company, accession)` is meant; when given it is sent and the
 * response must name the same registrant. There is exactly one request: a qualified request that
 * fails or is refused is NEVER retried without the CIK, which would hide a joint accession's
 * ambiguity or show another registrant's filing.
 */
export function getFilingEps(
  accession: string,
  periodClass: EpsPeriodClass,
  epsKind: EpsKind,
  cik: string | null = null,
  options: ApiGetOptions = {},
): Promise<FilingsResult<FilingEps>> {
  return guard(async () => {
    const eps = parseFilingEps(
      await apiGet<unknown>(
        buildFilingEpsPath(accession, periodClass, epsKind, cik),
        options,
      ),
    );
    // A response for another accession or EPS concept must never be shown under this request.
    if (
      eps.accession !== accession ||
      eps.period_class !== periodClass ||
      eps.eps_kind !== epsKind
    ) {
      throw new FilingsContractError(
        "the response does not echo the requested selection.",
      );
    }
    if (cik !== null && eps.company_cik !== cik) {
      throw new FilingsContractError(
        "the response does not name the requested registrant (CIK).",
      );
    }
    return eps;
  });
}
