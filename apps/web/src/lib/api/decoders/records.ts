/**
 * Strict decoders of the saved-research-record endpoints: the list, one record, and a
 * verification. Same rules as every decoder here (decode.ts): every documented key present, null
 * a value and absent drift, an unknown enum member rejected, decimals exact strings.
 *
 * Two kinds of check are made, and the second is deliberately small:
 *
 * - shapes: the summary, the stored manifest and the stored result, down to every field a page
 *   shows. The OpenAPI schema declares the manifest and result as open objects, so these decoders
 *   (and the captured backend fixtures) are the only authority for them;
 * - coherence the API itself documents: COMPLETE exactly when no gap is listed, a verification's
 *   changed-path lists matching the kind of status it carries, the detail's request repeating its
 *   manifest's request. A response that breaks one of these is refused as contract drift.
 *
 * Nothing here DECIDES anything the backend decides: the level, the gaps and the verification
 * status are read from the response and never derived from the manifest, the result or the
 * fingerprints.
 */

import {
  arrayOf,
  decimalString,
  integer,
  isoDate,
  isoInstant,
  literal,
  matching,
  nonEmptyString,
  nonNegativeInteger,
  nullable,
  object,
  oneOf,
  refine,
  reject,
  string,
  uuid,
  type Decoder,
} from "@/lib/api/decode";
import {
  abnormalReturn,
  accession,
  alignment,
  benchmarkReturn,
  checkStudyCoherence,
  cik,
  disclosure,
  epsSurprise,
  event,
  exact,
  filingLink,
  horizonList,
  listingResolution,
  maybeUuid,
  stockReturns,
} from "@/lib/api/decoders/research";
import {
  EVIDENCE_STATES,
  type AccountingEvidenceManifest,
  type BarPin,
  type BenchmarkEvidenceManifest,
  type EventEvidenceManifest,
  type FactPin,
  type FilingEvidenceManifest,
  type ManifestEvidence,
  type ManifestMethodology,
  type ManifestRequest,
  type PitEvidenceManifest,
  type RecordDetail,
  type SessionsPin,
  type StockEvidenceManifest,
  type StoredAccounting,
  type StoredComparativeEps,
  type StoredCurrentEps,
  type StoredEpsChange,
  type StoredFact,
  type StoredIdentity,
  type StoredManifest,
  type StoredPointInTime,
  type StoredResult,
  type StoredStudy,
} from "@/lib/api/record-types";
import {
  ARTIFACT_ROLES,
  AVAILABILITY_BASES,
  ACCOUNTING_STATES,
  CHANGE_STATUSES,
  COMPARATIVE_STATUSES,
  DISCLOSURE_LEVELS,
  EARNINGS_TIMINGS,
  EPS_KINDS,
  EPS_STATUSES,
  FILING_FORMS,
  HASH_ALGORITHM,
  LISTING_EVIDENCE,
  LISTING_RESOLUTION_STATUSES,
  MANIFEST_SECTIONS,
  PAGE_LIMIT_MAX,
  PAGE_OFFSET_MAX,
  PERCENTAGE_REASONS,
  PERIOD_CLASSES,
  PIT_ELIGIBILITIES,
  PRICE_BASES,
  REPRODUCIBILITY_GAPS,
  RESEARCH_MODES,
  TRANSITIONS,
  VERIFICATION_BASELINES,
  VERIFICATION_STATUSES,
  type RecordFingerprints,
  type RecordPage,
  type RecordRequest,
  type RecordSummary,
  type Verification,
  type VerificationSide,
} from "@/lib/api/schema";
import { REPRODUCIBILITY_LEVELS } from "@/lib/state";

const sha256 = matching(/^[0-9a-f]{64}$/, "a SHA-256 hex digest");
const form = oneOf(FILING_FORMS, "filing form");
const maybeText = nullable(string);
const maybeSha = nullable(sha256);

/** A two-element JSON array, as the canonical serialiser writes a tuple. */
function pair<A, B>(first: Decoder<A>, second: Decoder<B>): Decoder<readonly [A, B]> {
  return (value, path) => {
    if (!Array.isArray(value) || value.length !== 2) return reject(path, "a pair [name, value]");
    return [first(value[0], `${path}[0]`), second(value[1], `${path}[1]`)] as const;
  };
}

// ── summary (the list row, and the head of the detail) ───────────────────────────────────

const requestSummary = object<RecordRequest>({
  event_id: uuid,
  horizons: horizonList,
  benchmark_id: maybeUuid,
  security_id: maybeUuid,
  filing_accession: nullable(accession),
  eps_period_class: nullable(oneOf(PERIOD_CLASSES, "period class")),
  eps_kind: nullable(oneOf(EPS_KINDS, "EPS kind")),
});

const level = oneOf(REPRODUCIBILITY_LEVELS, "reproducibility level");
const gaps = refine(arrayOf(oneOf(REPRODUCIBILITY_GAPS, "reproducibility gap")), (list, path) => {
  if (new Set(list).size !== list.length) reject(path, "reproducibility gaps without duplicates");
});

/** The documented rule: the level is derived from the gaps, so COMPLETE has none and PARTIAL has some. */
function levelMatchesGaps(value: { reproducibility_level: string; reproducibility_gaps: readonly string[] }, path: string): void {
  if ((value.reproducibility_level === "COMPLETE") !== (value.reproducibility_gaps.length === 0)) {
    reject(`${path}.reproducibility_gaps`, "no gaps exactly when the level is COMPLETE");
  }
}

const summaryShape = {
  record_id: uuid,
  created_at: isoInstant,
  hash_algorithm: literal(HASH_ALGORITHM),
  manifest_fingerprint: sha256,
  result_fingerprint: sha256,
  reproducibility_level: level,
  reproducibility_gaps: gaps,
  request: requestSummary,
} as const;

const summary: Decoder<RecordSummary> = refine(object<RecordSummary>(summaryShape), levelMatchesGaps);

/** `GET /api/v1/research/records`. */
export const decodeRecordPage: Decoder<RecordPage> = refine(
  object<RecordPage>({
    items: arrayOf(summary),
    total: nonNegativeInteger,
    limit: integer(1, PAGE_LIMIT_MAX),
    offset: integer(0, PAGE_OFFSET_MAX),
  }),
  (page, path) => {
    if (page.items.length > page.limit) reject(`${path}.items`, "no more items than the page limit");
  },
);

// ── stored manifest ──────────────────────────────────────────────────────────────────────

const evidenceState = oneOf(EVIDENCE_STATES, "evidence state");

const manifestRequest = object<ManifestRequest>({
  event_id: uuid,
  horizons: horizonList,
  benchmark_id: maybeUuid,
  benchmark_source_id: maybeUuid,
  security_id: maybeUuid,
  stock_source_id: maybeUuid,
  price_basis: oneOf(PRICE_BASES, "price basis"),
  filing_accession: nullable(accession),
  eps_period_class: nullable(oneOf(PERIOD_CLASSES, "period class")),
  eps_kind: nullable(oneOf(EPS_KINDS, "EPS kind")),
  modes: arrayOf(oneOf(RESEARCH_MODES, "research mode")),
});

const methodology = object<ManifestMethodology>({
  components: arrayOf(pair(nonEmptyString, nonEmptyString)),
  approved_availability_bases: arrayOf(oneOf(AVAILABILITY_BASES, "availability basis")),
  alignment_window_days: integer(0),
});

const barPin = object<BarPin>({
  session: isoDate,
  bar_id: uuid,
  source_id: uuid,
  close: decimalString,
  ingested_at: isoInstant,
  ingestion_run_id: maybeUuid,
});

const sessionsPin = object<SessionsPin>({
  window_start: isoDate,
  window_end: isoDate,
  session_count: nonNegativeInteger,
  sessions_sha256: sha256,
});

const eventEvidence = object<EventEvidenceManifest>({
  event_id: uuid,
  fingerprint: nonEmptyString,
  source_id: uuid,
  ingestion_run_id: maybeUuid,
  event_date: isoDate,
  timing: oneOf(EARNINGS_TIMINGS, "earnings timing"),
});

const pitEvidence = object<PitEvidenceManifest>({
  eligibility: oneOf(PIT_ELIGIBILITIES, "point-in-time eligibility"),
  observed_at: nullable(isoInstant),
  observed_at_basis: nullable(oneOf(AVAILABILITY_BASES, "availability basis")),
});

const stockEvidence = object<StockEvidenceManifest>({
  state: evidenceState,
  listing_id: maybeUuid,
  security_id: maybeUuid,
  price_basis: oneOf(PRICE_BASES, "price basis"),
  sessions: nullable(sessionsPin),
  price_source_id: maybeUuid,
  bars: arrayOf(barPin),
});

const benchmarkEvidence = object<BenchmarkEvidenceManifest>({
  state: evidenceState,
  benchmark_id: maybeUuid,
  price_basis: oneOf(PRICE_BASES, "price basis"),
  window_start: nullable(isoDate),
  window_end: nullable(isoDate),
  session_count: nullable(nonNegativeInteger),
  sessions_sha256: maybeSha,
  bars: arrayOf(barPin),
});

const filingEvidence = object<FilingEvidenceManifest>({
  state: evidenceState,
  accession_number: nullable(accession),
  form: nullable(form),
  source_id: maybeUuid,
  ingestion_run_id: maybeUuid,
  items: maybeText,
  disclosure_level: oneOf(DISCLOSURE_LEVELS, "disclosure-evidence level"),
});

const factPin = object<FactPin>({
  role: oneOf(ARTIFACT_ROLES, "artifact role"),
  fact_id: uuid,
  tag: nonEmptyString,
  taxonomy_version: nonEmptyString,
  unit: nonEmptyString,
  period_end: isoDate,
  qtrs: nonNegativeInteger,
  value: exact,
  segments: string,
  coreg: string,
  artifact_sha256: sha256,
});

const accountingEvidence = object<AccountingEvidenceManifest>({
  state: evidenceState,
  artifacts: arrayOf(pair(nonEmptyString, sha256)),
  facts: arrayOf(factPin),
});

const evidence = object<ManifestEvidence>({
  event: eventEvidence,
  listing_resolution_status: oneOf(LISTING_RESOLUTION_STATUSES, "listing resolution status"),
  listing_evidence: nullable(oneOf(LISTING_EVIDENCE, "listing evidence")),
  pit: pitEvidence,
  stock: stockEvidence,
  benchmark: benchmarkEvidence,
  filing: filingEvidence,
  accounting: accountingEvidence,
});

const manifest = object<StoredManifest>({ request: manifestRequest, methodology, evidence });

// ── stored result ────────────────────────────────────────────────────────────────────────

const storedStudy: Decoder<StoredStudy> = refine(
  object<StoredStudy>({
    research_mode: oneOf(RESEARCH_MODES, "research mode"),
    point_in_time_eligibility: oneOf(PIT_ELIGIBILITIES, "point-in-time eligibility"),
    eps_surprise: epsSurprise,
    alignment,
    stock_returns: stockReturns,
    benchmark_id: maybeUuid,
    benchmark_returns: arrayOf(benchmarkReturn),
    abnormal_returns: arrayOf(abnormalReturn),
    price_basis_note: nonEmptyString,
  }),
  (value, path) =>
    checkStudyCoherence(
      { stock: value.stock_returns.forward_returns, benchmark: value.benchmark_returns, abnormal: value.abnormal_returns },
      path,
      { stock: "stock_returns.forward_returns", benchmark: "benchmark_returns", abnormal: "abnormal_returns" },
    ),
);

const storedFact = object<StoredFact>({
  fact_id: uuid,
  tag: nonEmptyString,
  taxonomy_version: nonEmptyString,
  unit: nonEmptyString,
  period_end: isoDate,
  duration_quarters: nonNegativeInteger,
  value: exact,
  segments: string,
  coreg: string,
});

const storedCurrent: Decoder<StoredCurrentEps> = refine(
  object<StoredCurrentEps>({
    status: oneOf(EPS_STATUSES, "EPS status"),
    reason: nonEmptyString,
    fact_id: maybeUuid,
    tag: nullable(nonEmptyString),
    taxonomy_version: nullable(nonEmptyString),
    unit: nullable(nonEmptyString),
    period_end: nullable(isoDate),
    duration_quarters: nullable(nonNegativeInteger),
    value: exact,
    segments: maybeText,
    coreg: maybeText,
    accession_number: accession,
    company_cik: nullable(cik),
    form: nullable(form),
    period_class: oneOf(PERIOD_CLASSES, "period class"),
    eps_kind: oneOf(EPS_KINDS, "EPS kind"),
  }),
  (current, path) => {
    // A stored fact carries its whole identity or none of it (it is displayed as one fact).
    if (current.fact_id !== null) {
      const whole = [current.tag, current.taxonomy_version, current.unit, current.period_end, current.duration_quarters, current.segments, current.coreg];
      if (whole.some((part) => part === null)) reject(`${path}.fact_id`, "a stored fact with its tag, unit, period and dimensions");
    }
  },
);

const storedComparative = object<StoredComparativeEps>({
  status: oneOf(COMPARATIVE_STATUSES, "comparative EPS status"),
  reason: nonEmptyString,
  comparative_fact: nullable(storedFact),
  accession_number: accession,
  company_cik: nullable(cik),
  form: nullable(form),
  period_class: oneOf(PERIOD_CLASSES, "period class"),
  eps_kind: oneOf(EPS_KINDS, "EPS kind"),
  current: storedCurrent,
});

const storedChange = object<StoredEpsChange>({
  status: oneOf(CHANGE_STATUSES, "EPS change status"),
  eps_change: exact,
  percentage_change: exact,
  percentage_unavailable_reason: nullable(oneOf(PERCENTAGE_REASONS, "percentage reason")),
  transition: nullable(oneOf(TRANSITIONS, "EPS transition")),
  comparative: storedComparative,
});

const storedAccounting: Decoder<StoredAccounting> = refine(
  object<StoredAccounting>({
    state: oneOf(ACCOUNTING_STATES, "accounting state"),
    change: nullable(storedChange),
  }),
  (section, path) => {
    if ((section.state === "EVALUATED") !== (section.change !== null)) {
      reject(`${path}.change`, "the stored EPS change exactly when the accounting state is EVALUATED");
    }
  },
);

const storedIdentity = object<StoredIdentity>({
  company_id: uuid,
  company_cik: nullable(cik),
  event_id: uuid,
  listing_resolution: listingResolution,
  benchmark_id: maybeUuid,
  filing_accession: nullable(accession),
});

const storedPointInTime: Decoder<StoredPointInTime> = refine(
  object<StoredPointInTime>({
    eligibility: oneOf(PIT_ELIGIBILITIES, "point-in-time eligibility"),
    study: nullable(storedStudy),
  }),
  (section, path) => {
    // The point-in-time study exists exactly when the stored evidence was eligible.
    if ((section.eligibility === "ELIGIBLE") !== (section.study !== null)) {
      reject(`${path}.study`, "a point-in-time study exactly when the eligibility is ELIGIBLE");
    }
  },
);

const result = object<StoredResult>({
  event,
  identity: storedIdentity,
  filing_link: filingLink,
  disclosure_evidence: disclosure,
  accounting: storedAccounting,
  descriptive: object<{ readonly study: StoredStudy }>({ study: storedStudy }),
  point_in_time: storedPointInTime,
});

// ── detail ───────────────────────────────────────────────────────────────────────────────

const sameList = (a: readonly number[], b: readonly number[]) => a.length === b.length && a.every((value, index) => value === b[index]);

/** `GET /api/v1/research/records/{id}`. */
export const decodeRecordDetail: Decoder<RecordDetail> = refine(
  object<RecordDetail>({ ...summaryShape, manifest, result }),
  (record, path) => {
    levelMatchesGaps(record, path);
    // The detail repeats the manifest's own request in its head: they must be one request.
    const head = record.request;
    const stored = record.manifest.request;
    const same =
      head.event_id.toLowerCase() === stored.event_id.toLowerCase() &&
      sameList(head.horizons, stored.horizons) &&
      head.benchmark_id === stored.benchmark_id &&
      head.security_id === stored.security_id &&
      head.filing_accession === stored.filing_accession &&
      head.eps_period_class === stored.eps_period_class &&
      head.eps_kind === stored.eps_kind;
    if (!same) reject(`${path}.request`, "the request to equal the stored manifest's request");
    if (record.result.event.event_id.toLowerCase() !== stored.event_id.toLowerCase()) {
      reject(`${path}.result.event.event_id`, "the stored result to describe the request's event");
    }
  },
);

// ── verification ─────────────────────────────────────────────────────────────────────────

const fingerprints = object<RecordFingerprints>({
  request: sha256,
  methodology: sha256,
  evidence: sha256,
  manifest: sha256,
  result: sha256,
});

const side: Decoder<VerificationSide> = refine(
  object<VerificationSide>({
    reproducibility_level: level,
    reproducibility_gaps: gaps,
    fingerprints,
  }),
  levelMatchesGaps,
);

const changedPath = nonEmptyString;

/**
 * `GET /api/v1/research/records/{id}/verify`. The status is the verifier's own and is only ever
 * read here. The coherence rules below only refuse a report whose parts contradict its status (a
 * MATCH that lists changed paths, a result-only change that lists manifest paths); a changed status
 * with an empty path list is shown as it came.
 */
export const decodeVerification: Decoder<Verification> = refine(
  object<Verification>({
    record_id: uuid,
    status: oneOf(VERIFICATION_STATUSES, "verification status"),
    baseline: oneOf(VERIFICATION_BASELINES, "verification baseline"),
    reproducibility_level: level,
    changed_sections: refine(arrayOf(oneOf(MANIFEST_SECTIONS, "manifest section")), (list, path) => {
      if (new Set(list).size !== list.length) reject(path, "changed sections without duplicates");
    }),
    changed_paths: arrayOf(changedPath),
    changed_result_paths: arrayOf(changedPath),
    expected: side,
    actual: side,
  }),
  (report, path) => {
    // "Level of the rerun (actual)".
    if (report.reproducibility_level !== report.actual.reproducibility_level) {
      reject(`${path}.reproducibility_level`, "the level of the rerun (actual)");
    }
    const manifestChanges = report.changed_sections.length + report.changed_paths.length;
    // The two "changed" statuses are shown with whatever paths the verifier listed (possibly none).
    // What is refused is a report whose parts contradict its status: a status that names no change
    // while paths are listed, or a result-only change that lists manifest paths.
    switch (report.status) {
      case "RESULT_CHANGED":
        if (manifestChanges !== 0) reject(`${path}.changed_paths`, "no manifest change for RESULT_CHANGED");
        break;
      case "EVIDENCE_INCOMPLETE":
      case "MATCH":
        if (manifestChanges !== 0 || report.changed_result_paths.length !== 0) {
          reject(`${path}.changed_paths`, `no changed paths for ${report.status}`);
        }
        break;
      case "MANIFEST_CHANGED":
        break;
    }
  },
);
