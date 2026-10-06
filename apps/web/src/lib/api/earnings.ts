/**
 * Earnings and readiness reads. Every function resolves to an `ApiResult` (see result.ts): a
 * page always renders an explicit state, and transport failures are never confused with domain
 * statuses carried inside a successful response.
 */

import { cache } from "react";

import { apiRequest, type ApiRequestOptions } from "@/lib/api/client";
import { decode, uuid } from "@/lib/api/decode";
import { decodeEarningsEvent, decodeEarningsPage } from "@/lib/api/decoders/earnings";
import { decodeReadiness } from "@/lib/api/decoders/health";
import { requireEcho } from "@/lib/api/echo";
import type { ApiResult } from "@/lib/api/result";
import { PAGE_SIZE, type EarningsEvent, type EarningsPage, type ReadinessResponse } from "@/lib/api/schema";
import { env } from "@/lib/config/env";

/** Query parameters the earnings list endpoint supports (and only those). */
export interface EarningsQuery {
  readonly ticker?: string;
  readonly dateFrom?: string;
  readonly dateTo?: string;
  readonly offset?: number;
  readonly limit?: number;
}

export function buildEarningsPath(query: EarningsQuery): string {
  const params = new URLSearchParams();
  if (query.ticker) params.set("ticker", query.ticker);
  if (query.dateFrom) params.set("date_from", query.dateFrom);
  if (query.dateTo) params.set("date_to", query.dateTo);
  params.set("limit", String(query.limit ?? PAGE_SIZE));
  params.set("offset", String(query.offset ?? 0));
  return `${env.apiEarningsPath}?${params.toString()}`;
}

export async function getEarningsPage(
  query: EarningsQuery = {},
  options: ApiRequestOptions = {},
): Promise<ApiResult<EarningsPage>> {
  const limit = query.limit ?? PAGE_SIZE;
  const offset = query.offset ?? 0;
  const result = await apiRequest(buildEarningsPath(query), decodeEarningsPage, options);
  return requireEcho(result, (page) =>
    page.limit !== limit
      ? { path: "$.limit", expected: "the requested limit" }
      : page.offset !== offset
        ? { path: "$.offset", expected: "the requested offset" }
        : null,
  );
}

/** A path that is not a UUID names nothing stored, so no request is made for it. */
export async function getEarningsEvent(
  id: string,
  options: ApiRequestOptions = {},
): Promise<ApiResult<EarningsEvent>> {
  if (!decode(uuid, id).ok) return { kind: "not_found" };
  const result = await apiRequest(`${env.apiEarningsPath}/${encodeURIComponent(id)}`, decodeEarningsEvent, { ...options, notFoundMeansAbsent: true });
  return requireEcho(result, (event) =>
    event.id.toLowerCase() !== id.toLowerCase() ? { path: "$.id", expected: "the requested event id" } : null,
  );
}

/** Readiness answers 503 with a structured body when a dependency is down: still a decoded success. */
export function getReadiness(options: ApiRequestOptions = {}): Promise<ApiResult<ReadinessResponse>> {
  return apiRequest(env.apiReadinessPath, decodeReadiness, { ...options, acceptStatuses: [503] });
}

/**
 * One event read shared by everything rendered for a request (the page and its metadata both
 * ask), so a detail view costs ONE API call.
 */
export const getEarningsEventOnce = cache((id: string) => getEarningsEvent(id));
