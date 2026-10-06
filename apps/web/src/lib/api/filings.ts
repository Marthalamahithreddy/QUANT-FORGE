/**
 * Read-only filings reads: the FSDS-backed filings index and one filing's descriptive EPS.
 *
 * Every function resolves to an `ApiResult`. Only GET is ever issued, and only against the
 * QuantForge API.
 */

import { apiRequest, type ApiRequestOptions } from "@/lib/api/client";
import { decodeFilingEps, decodeFilingIndexPage } from "@/lib/api/decoders/filings";
import { requireEcho } from "@/lib/api/echo";
import type { ApiResult } from "@/lib/api/result";
import {
  PAGE_SIZE,
  type EpsKind,
  type EpsPeriodClass,
  type FilingEps,
  type FilingForm,
  type FilingIndexPage,
} from "@/lib/api/schema";
import { env } from "@/lib/config/env";

/** Query parameters the filings index supports (and only those). */
export interface FilingsQuery {
  readonly form?: FilingForm;
  readonly cik?: string;
  readonly offset?: number;
  readonly limit?: number;
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
  const params = new URLSearchParams({ period_class: periodClass, eps_kind: epsKind });
  // Registrant qualification: sent verbatim (leading zeros intact) only when the caller has one.
  if (cik !== null) params.set("cik", cik);
  return `${env.apiFilingsPath}/${encodeURIComponent(accession)}/eps?${params.toString()}`;
}

export async function getFilings(
  query: FilingsQuery = {},
  options: ApiRequestOptions = {},
): Promise<ApiResult<FilingIndexPage>> {
  const limit = query.limit ?? PAGE_SIZE;
  const offset = query.offset ?? 0;
  const result = await apiRequest(buildFilingsPath(query), decodeFilingIndexPage, options);
  return requireEcho(result, (page) =>
    page.limit !== limit
      ? { path: "$.limit", expected: "the requested limit" }
      : page.offset !== offset
        ? { path: "$.offset", expected: "the requested offset" }
        : null,
  );
}

/**
 * Both selectors are required and typed: nothing is defaulted or inferred here. `cik` optionally
 * qualifies WHICH stored filing `(company, accession)` is meant; when given it is sent and the
 * response must name the same registrant. There is exactly one request: a qualified request that
 * fails or is refused is NEVER retried without the CIK, which would hide a joint accession's
 * ambiguity or show another registrant's filing.
 */
export async function getFilingEps(
  accession: string,
  periodClass: EpsPeriodClass,
  epsKind: EpsKind,
  cik: string | null = null,
  options: ApiRequestOptions = {},
): Promise<ApiResult<FilingEps>> {
  const result = await apiRequest(buildFilingEpsPath(accession, periodClass, epsKind, cik), decodeFilingEps, options);
  // A response for another accession or EPS concept must never be shown under this request.
  return requireEcho(result, (eps) =>
    eps.accession !== accession
      ? { path: "$.accession", expected: "the requested accession" }
      : eps.period_class !== periodClass
        ? { path: "$.period_class", expected: "the requested period class" }
        : eps.eps_kind !== epsKind
          ? { path: "$.eps_kind", expected: "the requested EPS kind" }
          : cik !== null && eps.company_cik !== cik
            ? { path: "$.company_cik", expected: "the requested registrant (CIK)" }
            : null,
  );
}
