/**
 * Saved-research reads: the record list, one stored record, and a verification.
 *
 * All three are GETs and none writes. A record is an immutable stored baseline; the verification
 * is a computation against CURRENT stored evidence whose outcome is returned and never saved.
 * Nothing here can create, change or delete a record, and no function takes a body.
 *
 * Timeouts: the list and the single record are ordinary indexed reads (the 4 s class). Only the
 * verification re-assembles the observation, so it uses the longer computed class.
 *
 * Failure is kept apart from the domain result: a 404 (no such record), a 409 (the record's event
 * is no longer stored, so it cannot be re-run), a 500 (the stored record failed its integrity
 * validation) and a 503 are failures. MATCH, EVIDENCE_INCOMPLETE, MANIFEST_CHANGED and
 * RESULT_CHANGED are successful answers (HTTP 200) and are never mapped onto a failure.
 */

import { OBSERVATION_TIMEOUT_MS, apiRequest, type ApiRequestOptions } from "@/lib/api/client";
import { decode, uuid } from "@/lib/api/decode";
import { decodeRecordDetail, decodeRecordPage, decodeVerification } from "@/lib/api/decoders/records";
import { requireEcho } from "@/lib/api/echo";
import type { RecordDetail } from "@/lib/api/record-types";
import type { ApiResult } from "@/lib/api/result";
import { RECORD_PAGE_SIZE, type RecordPage, type Verification } from "@/lib/api/schema";
import { env } from "@/lib/config/env";

export interface RecordListQuery {
  readonly limit?: number;
  readonly offset?: number;
}

export function buildRecordListPath(query: RecordListQuery = {}): string {
  const params = new URLSearchParams();
  params.set("limit", String(query.limit ?? RECORD_PAGE_SIZE));
  params.set("offset", String(query.offset ?? 0));
  return `${env.apiRecordsPath}?${params.toString()}`;
}

export async function getRecordPage(query: RecordListQuery = {}, options: ApiRequestOptions = {}): Promise<ApiResult<RecordPage>> {
  const limit = query.limit ?? RECORD_PAGE_SIZE;
  const offset = query.offset ?? 0;
  const result = await apiRequest(buildRecordListPath(query), decodeRecordPage, options);
  return requireEcho(result, (page) =>
    page.limit !== limit
      ? { path: "$.limit", expected: "the requested limit" }
      : page.offset !== offset
        ? { path: "$.offset", expected: "the requested offset" }
        : null,
  );
}

const sameId = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

/** A path that is not a UUID names no record, so no request is made for it. */
export async function getRecord(id: string, options: ApiRequestOptions = {}): Promise<ApiResult<RecordDetail>> {
  if (!decode(uuid, id).ok) return { kind: "not_found" };
  const result = await apiRequest(`${env.apiRecordsPath}/${encodeURIComponent(id)}`, decodeRecordDetail, {
    ...options,
    notFoundMeansAbsent: true,
  });
  return requireEcho(result, (record) => (sameId(record.record_id, id) ? null : { path: "$.record_id", expected: "the requested record id" }));
}

/**
 * Re-runs the record's own request against the current stored evidence and compares it with the
 * stored baseline. Read-only: nothing is saved and the record is not touched.
 */
export async function verifyRecord(id: string, options: ApiRequestOptions = {}): Promise<ApiResult<Verification>> {
  if (!decode(uuid, id).ok) return { kind: "not_found" };
  const result = await apiRequest(`${env.apiRecordsPath}/${encodeURIComponent(id)}/verify`, decodeVerification, {
    timeoutMs: OBSERVATION_TIMEOUT_MS,
    ...options,
    notFoundMeansAbsent: true,
  });
  return requireEcho(result, (report) => (sameId(report.record_id, id) ? null : { path: "$.record_id", expected: "the requested record id" }));
}
