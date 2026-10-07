/**
 * Saved research: URL state and the "re-run live" address.
 *
 * Two views, two addresses:
 *
 * - the list, `/research/saved`, whose only state is the page (`page`, defaults omitted). The page
 *   size is fixed (RECORD_PAGE_SIZE), so no `limit` parameter exists;
 * - one record, `/research/saved/{record_id}`, which shows the stored baseline only, and
 *   `?verify=1`, which additionally asks the API to re-run it. Verification is therefore an
 *   explicit act with its own address (refresh, back/forward and sharing all work).
 *
 * "Re-run live" is a link, never a request: it builds the Research workspace's own address from the
 * stored request and issues nothing.
 */

import type { ObservationQuery } from "@/lib/api/research";
import type { ManifestRequest } from "@/lib/api/record-types";
import { RECORD_PAGE_SIZE, type RecordRequest } from "@/lib/api/schema";
import { canonicalHref, isCanonical, offsetOfPage, parsePage, readParam, type ListError, type RawSearchParams } from "@/lib/list-query";
import { researchHref } from "@/lib/research-view";

export const SAVED_PATH = "/research/saved";

export const recordPath = (recordId: string): string => `${SAVED_PATH}/${recordId}`;

/** The address of one record; `verify` adds the explicit re-run request. */
export const recordHref = (recordId: string, verify = false): string => (verify ? `${recordPath(recordId)}?verify=1` : recordPath(recordId));

export const offsetOfRecordPage = (page: number): number => offsetOfPage(page, RECORD_PAGE_SIZE);

export const savedPageHref = (page: number): string => canonicalHref(SAVED_PATH, [], page);

export interface SavedListState {
  readonly page: number;
  readonly errors: readonly ListError[];
  /** Canonical address of this view; `null` when the URL is invalid. */
  readonly canonical: string | null;
  readonly redirect: boolean;
}

export function parseSavedList(raw: RawSearchParams): SavedListState {
  const { page, error } = parsePage(raw);
  const errors = error ? [error] : [];
  const canonical = errors.length === 0 ? savedPageHref(page) : null;
  return { page, errors, canonical, redirect: canonical !== null && !isCanonical(raw, canonical) };
}

export interface RecordViewState {
  /** The explicit request to re-run this record. */
  readonly verify: boolean;
  readonly errors: readonly ListError[];
  readonly canonical: string | null;
  readonly redirect: boolean;
}

/** `?verify=1` is the only accepted form; `verify=` is the default (dropped), anything else an error. */
export function parseRecordView(recordId: string, raw: RawSearchParams): RecordViewState {
  const param = readParam(raw, "verify");
  const errors: ListError[] = [];
  let verify = false;
  if (param.repeated || (param.text !== "" && param.text !== "1")) {
    errors.push({ param: "verify", message: "The verify parameter accepts only the value 1, as a single value." });
  } else if (param.text === "1") {
    verify = true;
  }
  const canonical = errors.length === 0 ? recordHref(recordId, verify) : null;
  return { verify, errors, canonical, redirect: canonical !== null && !isCanonical(raw, canonical) };
}

/** The live workspace's query for a stored request: only parameters the workspace genuinely accepts. */
export function rerunQuery(request: RecordRequest): ObservationQuery {
  return {
    horizons: request.horizons,
    benchmarkId: request.benchmark_id ?? undefined,
    filingAccession: request.filing_accession ?? undefined,
    epsPeriodClass: request.filing_accession ? (request.eps_period_class ?? undefined) : undefined,
    epsKind: request.filing_accession ? (request.eps_kind ?? undefined) : undefined,
  };
}

/** `/research/events/{event}?...`: a NEW live study of the same request. It carries no record id, fingerprint or evidence id. */
export const rerunHref = (request: RecordRequest): string => researchHref(request.event_id, rerunQuery(request));

/**
 * Inputs a stored request can pin that the live workspace does not accept (it never sends a
 * `security_id`, and never pins a price or benchmark source). The live study will resolve these
 * itself, so the link says so rather than implying an identical request.
 */
export function pinsTheWorkspaceCannotCarry(request: Pick<ManifestRequest, "security_id" | "stock_source_id" | "benchmark_source_id"> | Pick<RecordRequest, "security_id">): readonly string[] {
  const pins: string[] = [];
  if (request.security_id !== null) pins.push("a security");
  if ("stock_source_id" in request && request.stock_source_id !== null) pins.push("a stock price source");
  if ("benchmark_source_id" in request && request.benchmark_source_id !== null) pins.push("a benchmark price source");
  return pins;
}
