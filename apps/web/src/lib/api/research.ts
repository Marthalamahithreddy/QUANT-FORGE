/**
 * Research reads: the benchmark index and ONE research observation.
 *
 * The observation already contains both research modes (descriptive and point-in-time), so a
 * workspace makes exactly one observation request, never one per mode, horizon or section. Only
 * parameters the caller names are sent: no benchmark and no filing is ever inferred.
 */

import { cache } from "react";

import { OBSERVATION_TIMEOUT_MS, apiRequest, type ApiRequestOptions } from "@/lib/api/client";
import { decode, uuid } from "@/lib/api/decode";
import { decodeBenchmarkPage, decodeObservation } from "@/lib/api/decoders/research";
import { requireEcho } from "@/lib/api/echo";
import type { ApiResult } from "@/lib/api/result";
import {
  DEFAULT_HORIZONS,
  PAGE_LIMIT_MAX,
  type BenchmarkPage,
  type EpsKind,
  type EpsPeriodClass,
  type Observation,
} from "@/lib/api/schema";
import { env } from "@/lib/config/env";

/** The parameters the observation endpoint accepts that this interface can send. */
export interface ObservationQuery {
  /** Absent: the research default (1, 5, 20). */
  readonly horizons?: readonly number[];
  /** Absent: no benchmark. */
  readonly benchmarkId?: string;
  /** Absent: no filing (NOT_LINKED). Requires both selectors. */
  readonly filingAccession?: string;
  readonly epsPeriodClass?: EpsPeriodClass;
  readonly epsKind?: EpsKind;
}

export function buildObservationPath(eventId: string, query: ObservationQuery = {}): string {
  const params = new URLSearchParams();
  if (query.horizons && query.horizons.length > 0) params.set("horizons", query.horizons.join(","));
  if (query.benchmarkId) params.set("benchmark_id", query.benchmarkId);
  if (query.filingAccession) {
    params.set("filing_accession", query.filingAccession);
    if (query.epsPeriodClass) params.set("eps_period_class", query.epsPeriodClass);
    if (query.epsKind) params.set("eps_kind", query.epsKind);
  }
  const suffix = params.toString();
  return `${env.apiObservationsPath}/${encodeURIComponent(eventId)}${suffix ? `?${suffix}` : ""}`;
}

/** The horizons the API will echo: the request's, ascending, or the default. */
export function effectiveHorizons(query: ObservationQuery): readonly number[] {
  return query.horizons && query.horizons.length > 0 ? [...query.horizons].sort((a, b) => a - b) : DEFAULT_HORIZONS;
}

const sameList = (a: readonly number[], b: readonly number[]) => a.length === b.length && a.every((value, index) => value === b[index]);

/**
 * One observation. A path that is not a UUID names nothing stored, so no request is made. Every
 * echoed input must equal what was asked: a response for another event, benchmark, filing or
 * horizon set is never shown under this request.
 */
export async function getObservation(
  eventId: string,
  query: ObservationQuery = {},
  options: ApiRequestOptions = {},
): Promise<ApiResult<Observation>> {
  if (!decode(uuid, eventId).ok) return { kind: "not_found" };
  const result = await apiRequest(buildObservationPath(eventId, query), decodeObservation, {
    timeoutMs: OBSERVATION_TIMEOUT_MS,
    ...options,
    notFoundMeansAbsent: true,
  });
  const wantsFiling = query.filingAccession !== undefined;
  return requireEcho(result, (observation) => {
    const { identity, parameters } = observation;
    if (identity.event_id.toLowerCase() !== eventId.toLowerCase()) return { path: "$.identity.event_id", expected: "the requested event id" };
    if (observation.event.event_id.toLowerCase() !== eventId.toLowerCase()) return { path: "$.event.event_id", expected: "the requested event id" };
    if (!sameList(parameters.horizons, effectiveHorizons(query))) return { path: "$.parameters.horizons", expected: "the requested horizons" };
    if ((parameters.benchmark_id ?? null) !== (query.benchmarkId ?? null) || (identity.benchmark_id ?? null) !== (query.benchmarkId ?? null)) {
      return { path: "$.parameters.benchmark_id", expected: "the requested benchmark (or none)" };
    }
    if ((parameters.filing_accession ?? null) !== (query.filingAccession ?? null) || (identity.filing_accession ?? null) !== (query.filingAccession ?? null)) {
      return { path: "$.parameters.filing_accession", expected: "the requested filing (or none)" };
    }
    if ((parameters.eps_period_class ?? null) !== (wantsFiling ? (query.epsPeriodClass ?? null) : null)) {
      return { path: "$.parameters.eps_period_class", expected: "the requested period class" };
    }
    if ((parameters.eps_kind ?? null) !== (wantsFiling ? (query.epsKind ?? null) : null)) {
      return { path: "$.parameters.eps_kind", expected: "the requested EPS kind" };
    }
    // Research reads split-adjusted prices only; any other basis is not this product's study.
    if (parameters.price_basis !== "SPLIT_ADJUSTED") return { path: "$.parameters.price_basis", expected: "SPLIT_ADJUSTED" };
    if (parameters.security_id !== null) return { path: "$.parameters.security_id", expected: "no security (none was requested)" };
    return null;
  });
}

/** One page of the benchmark index. The caller decides what to do about the rest of the universe. */
export async function getBenchmarks(
  page: { readonly limit?: number; readonly offset?: number } = {},
  options: ApiRequestOptions = {},
): Promise<ApiResult<BenchmarkPage>> {
  const limit = page.limit ?? PAGE_LIMIT_MAX;
  const offset = page.offset ?? 0;
  const result = await apiRequest(`${env.apiBenchmarksPath}?limit=${limit}&offset=${offset}`, decodeBenchmarkPage, options);
  return requireEcho(result, (data) =>
    data.limit !== limit ? { path: "$.limit", expected: "the requested limit" } : data.offset !== offset ? { path: "$.offset", expected: "the requested offset" } : null,
  );
}

/**
 * One benchmark-index page shared by everything rendered for a request (the selector, the context
 * bar and the result's benchmark name), so a workspace costs ONE benchmark call per page offset.
 */
export const getBenchmarksOnce = cache((offset: number) => getBenchmarks({ limit: PAGE_LIMIT_MAX, offset }));
