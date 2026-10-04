/**
 * Earnings and readiness reads.
 *
 * Every function resolves to a discriminated result instead of throwing, so a
 * page always renders an explicit loading-complete / error / not-found state.
 */

import { ApiError, apiGet, type ApiGetOptions } from "@/lib/api/client";
import { env } from "@/lib/config/env";
import {
  parseEarningsEvent,
  parseEarningsPage,
  parseReadiness,
  type EarningsEvent,
  type EarningsPage,
  type ReadinessResponse,
} from "@/types/earnings";

export type Result<T> =
  | { readonly state: "ok"; readonly data: T }
  | { readonly state: "not_found" }
  | { readonly state: "error"; readonly detail: string };

export const PAGE_SIZE = 50;

export interface EarningsQuery {
  readonly ticker?: string;
  readonly dateFrom?: string;
  readonly dateTo?: string;
  readonly offset?: number;
  readonly limit?: number;
}

function describe(error: unknown): string {
  // Never the raw message of a failed request: it names the service address.
  if (error instanceof ApiError) return error.publicMessage;
  return error instanceof Error ? error.message : "Unexpected error contacting the API.";
}

async function guard<T>(
  run: () => Promise<T>,
  notFoundStatuses: readonly number[] = [404],
): Promise<Result<T>> {
  try {
    return { state: "ok", data: await run() };
  } catch (error) {
    if (error instanceof ApiError && notFoundStatuses.includes(error.status)) {
      return { state: "not_found" };
    }
    return { state: "error", detail: describe(error) };
  }
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

export function getEarningsPage(
  query: EarningsQuery = {},
  options: ApiGetOptions = {},
): Promise<Result<EarningsPage>> {
  return guard(async () =>
    parseEarningsPage(await apiGet<unknown>(buildEarningsPath(query), options)),
  );
}

export function getEarningsEvent(
  id: string,
  options: ApiGetOptions = {},
): Promise<Result<EarningsEvent>> {
  // 422 on a path id means it is not a valid identifier: nothing to find.
  return guard(
    async () =>
      parseEarningsEvent(
        await apiGet<unknown>(`${env.apiEarningsPath}/${encodeURIComponent(id)}`, options),
      ),
    [404, 422],
  );
}

/** Readiness answers 503 with a structured body when a dependency is down. */
export function getReadiness(
  options: ApiGetOptions = {},
): Promise<Result<ReadinessResponse>> {
  return guard(async () =>
    parseReadiness(
      await apiGet<unknown>(env.apiReadinessPath, { ...options, acceptStatuses: [503] }),
    ),
  );
}
