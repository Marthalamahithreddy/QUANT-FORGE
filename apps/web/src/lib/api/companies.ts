/**
 * Read-only company reads: the directory search and one company's financials.
 *
 * Every function resolves to an `ApiResult`. Only GET is ever issued, and only against the
 * QuantForge API.
 */

import { apiRequest, type ApiRequestOptions } from "@/lib/api/client";
import { decodeCompanyDirectoryPage, decodeCompanyFinancials } from "@/lib/api/decoders/companies";
import { requireEcho } from "@/lib/api/echo";
import type { ApiResult } from "@/lib/api/result";
import { PAGE_SIZE, type CompanyDirectoryPage, type CompanyFinancials } from "@/lib/api/schema";
import { env } from "@/lib/config/env";

export interface CompaniesQuery {
  readonly q?: string;
  readonly offset?: number;
  readonly limit?: number;
}

export function buildCompaniesPath(query: CompaniesQuery): string {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  params.set("limit", String(query.limit ?? PAGE_SIZE));
  params.set("offset", String(query.offset ?? 0));
  return `${env.apiCompaniesPath}?${params.toString()}`;
}

export const buildCompanyFinancialsPath = (cik: string): string =>
  `${env.apiCompaniesPath}/${encodeURIComponent(cik)}/financials`;

export async function getCompanies(
  query: CompaniesQuery = {},
  options: ApiRequestOptions = {},
): Promise<ApiResult<CompanyDirectoryPage>> {
  const limit = query.limit ?? PAGE_SIZE;
  const offset = query.offset ?? 0;
  const result = await apiRequest(buildCompaniesPath(query), decodeCompanyDirectoryPage, options);
  return requireEcho(result, (page) =>
    page.limit !== limit
      ? { path: "$.limit", expected: "the requested limit" }
      : page.offset !== offset
        ? { path: "$.offset", expected: "the requested offset" }
        : null,
  );
}

/** A 404 means no company is stored under the CIK; a response for another CIK is never shown. */
export async function getCompanyFinancials(
  cik: string,
  options: ApiRequestOptions = {},
): Promise<ApiResult<CompanyFinancials>> {
  const result = await apiRequest(buildCompanyFinancialsPath(cik), decodeCompanyFinancials, {
    ...options,
    notFoundMeansAbsent: true,
  });
  return requireEcho(result, (data) =>
    data.company.cik !== cik ? { path: "$.company.cik", expected: "the requested CIK" } : null,
  );
}
