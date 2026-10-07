/**
 * Backend status -> domain state (design spec Appendix B), as exhaustive tables.
 *
 * `Record<Status, StateKind>` makes TypeScript reject a status without a mapping, and
 * tests/openapi-contract.test.ts compares each key set with the OpenAPI snapshot, so a backend
 * that adds an enum member fails the build until it is mapped here. The mapping decides only how
 * a status LOOKS (which of the ten states); the backend's own status code, label and reason
 * stay visible beside it, so distinct statuses (EXTENSION_ONLY, WRONG_UNIT, DIMENSIONAL_ONLY,
 * COREG_ONLY, MISSING...) are never collapsed into one generic "unavailable".
 */

import type {
  AbnormalReturnStatus,
  AccountingState,
  AlignmentStatus,
  BenchmarkReturnStatus,
  ChangeStatus,
  ComparativeStatus,
  EarningsTiming,
  EpsStatus,
  EpsSurpriseStatus,
  FilingLinkStatus,
  ForwardReturnStatus,
  ListingResolutionStatus,
  PercentageReason,
  PitEligibility,
  PointInTimeState,
  RefusalScope,
  SectionState,
} from "@/lib/api/schema";
import type { StateKind } from "@/lib/state";

export const SURPRISE_STATE: Record<EpsSurpriseStatus, StateKind> = {
  AVAILABLE: "VALUE",
  MISSING_REPORTED_EPS: "NOT_STATED",
  MISSING_ESTIMATED_EPS: "NOT_STATED",
  ZERO_ESTIMATE: "UNAVAILABLE",
  NOT_POINT_IN_TIME_ELIGIBLE: "REFUSED",
};

export const PIT_STATE: Record<PitEligibility, StateKind> = {
  ELIGIBLE: "VALUE",
  INELIGIBLE_NO_AVAILABILITY_EVIDENCE: "REFUSED",
  INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS: "REFUSED",
  NOT_APPLICABLE: "NOT_APPLICABLE",
};

export const EPS_STATE: Record<EpsStatus, StateKind> = {
  AVAILABLE: "VALUE",
  NOT_INGESTED: "MISSING",
  MISSING: "MISSING",
  UNSUPPORTED_FORM_PERIOD: "REFUSED",
  EXTENSION_ONLY: "REFUSED",
  WRONG_UNIT: "REFUSED",
  DIMENSIONAL_ONLY: "REFUSED",
  COREG_ONLY: "REFUSED",
  NULL_VALUE: "NOT_STATED",
  AMBIGUOUS: "AMBIGUOUS",
};

export const COMPARATIVE_STATE: Record<ComparativeStatus, StateKind> = {
  AVAILABLE: "VALUE",
  CURRENT_UNAVAILABLE: "UNAVAILABLE",
  COMPARATIVE_MISSING: "MISSING",
  COMPARATIVE_PERIOD_MISMATCH: "REFUSED",
  COMPARATIVE_DIMENSIONAL_ONLY: "REFUSED",
  COMPARATIVE_COREG_ONLY: "REFUSED",
  COMPARATIVE_WRONG_UNIT: "REFUSED",
  COMPARATIVE_NULL_VALUE: "NOT_STATED",
  COMPARATIVE_AMBIGUOUS: "AMBIGUOUS",
};

export const CHANGE_STATE: Record<ChangeStatus, StateKind> = {
  AVAILABLE: "VALUE",
  INPUT_UNAVAILABLE: "UNAVAILABLE",
};

/** A percentage that is undefined is UNAVAILABLE ("Undefined"), never `0%`. */
export const PERCENTAGE_STATE: Record<PercentageReason, StateKind> = {
  ZERO_COMPARATIVE: "UNAVAILABLE",
  NEGATIVE_COMPARATIVE: "UNAVAILABLE",
};

/** Only `UNKNOWN` is a non-value (the source stated no timing); the others are plain labels. */
export const TIMING_STATE: Record<EarningsTiming, StateKind> = {
  BEFORE_MARKET: "VALUE",
  DURING_MARKET: "VALUE",
  AFTER_MARKET: "VALUE",
  UNKNOWN: "NOT_STATED",
};

// ── research observation (design spec Appendix B) ─────────────────────────────────────────

export const ALIGNMENT_STATE: Record<AlignmentStatus, StateKind> = {
  ALIGNED: "VALUE",
  UNKNOWN_TIMING: "REFUSED",
  INTRADAY_PRECISION_UNSUPPORTED: "REFUSED",
  NO_SESSION_DATA: "MISSING",
  NOT_POINT_IN_TIME_ELIGIBLE: "REFUSED",
};

export const FORWARD_RETURN_STATE: Record<ForwardReturnStatus, StateKind> = {
  AVAILABLE: "VALUE",
  NO_ALIGNMENT: "UNAVAILABLE",
  MISSING_ANCHOR_PRICE: "MISSING",
  INSUFFICIENT_FORWARD_SESSIONS: "MISSING",
  MISSING_TARGET_PRICE: "MISSING",
  INVALID_PRICE: "REFUSED",
  AMBIGUOUS_PRICE_SOURCE: "AMBIGUOUS",
};

/** `NO_BENCHMARK` is NOT_REQUESTED: the reader chose none, so nothing failed. */
export const BENCHMARK_RETURN_STATE: Record<BenchmarkReturnStatus, StateKind> = {
  AVAILABLE: "VALUE",
  NO_BENCHMARK: "NOT_REQUESTED",
  NO_STOCK_RETURN: "UNAVAILABLE",
  MISSING_ANCHOR_PRICE: "MISSING",
  MISSING_TARGET_PRICE: "MISSING",
  INVALID_PRICE: "REFUSED",
  AMBIGUOUS_BENCHMARK_PRICE_SOURCE: "AMBIGUOUS",
};

/**
 * The abnormal return's own status. `BENCHMARK_RETURN_UNAVAILABLE` is NOT_REQUESTED instead of
 * UNAVAILABLE when the same horizon's benchmark status is `NO_BENCHMARK` (decided by the caller,
 * see `abnormalReturnState`).
 */
export const ABNORMAL_RETURN_STATE: Record<AbnormalReturnStatus, StateKind> = {
  AVAILABLE: "VALUE",
  STOCK_RETURN_UNAVAILABLE: "UNAVAILABLE",
  BENCHMARK_RETURN_UNAVAILABLE: "UNAVAILABLE",
};

export function abnormalReturnState(status: AbnormalReturnStatus, benchmarkStatus: BenchmarkReturnStatus | undefined): StateKind {
  return status === "BENCHMARK_RETURN_UNAVAILABLE" && benchmarkStatus === "NO_BENCHMARK" ? "NOT_REQUESTED" : ABNORMAL_RETURN_STATE[status];
}

export const SECTION_STATE: Record<SectionState, StateKind> = {
  AVAILABLE: "VALUE",
  PARTIAL: "PARTIAL",
  UNAVAILABLE: "UNAVAILABLE",
};

export const POINT_IN_TIME_STATE: Record<PointInTimeState, StateKind> = {
  EVIDENCE_ELIGIBLE: "VALUE",
  REFUSED: "REFUSED",
};

export const FILING_LINK_STATE: Record<FilingLinkStatus, StateKind> = {
  NOT_LINKED: "NOT_REQUESTED",
  FILING_NOT_STORED: "MISSING",
  CIK_MISMATCH_REFUSED: "REFUSED",
  CALLER_SUPPLIED_CIK_CONSISTENT: "VALUE",
};

export const ACCOUNTING_STATE: Record<AccountingState, StateKind> = {
  NOT_REQUESTED: "NOT_REQUESTED",
  LINK_REFUSED: "REFUSED",
  EVALUATED: "VALUE",
};

export const LISTING_STATE: Record<ListingResolutionStatus, StateKind> = {
  RESOLVED: "VALUE",
  NO_LISTING_FOR_DATE: "MISSING",
  AMBIGUOUS_LISTING: "AMBIGUOUS",
  CONFLICTING_HISTORY: "AMBIGUOUS",
};

/**
 * Which codes each refusal scope can carry: the originating layer's own status names (see
 * `observation/models.py` `_collect_refusals`). A code outside its scope's vocabulary is a
 * contract failure (the decoder rejects it) rather than a state this app invents.
 */
export const REFUSAL_CODE_STATE: Record<RefusalScope, Readonly<Record<string, StateKind>>> = {
  EVENT: SURPRISE_STATE,
  MARKET: { ...LISTING_STATE, ...ALIGNMENT_STATE, ...FORWARD_RETURN_STATE },
  BENCHMARK: BENCHMARK_RETURN_STATE,
  FILING_LINK: FILING_LINK_STATE,
  ACCOUNTING: { ...EPS_STATE, ...COMPARATIVE_STATE, ...PERCENTAGE_STATE },
  POINT_IN_TIME: { ...PIT_STATE, ...ALIGNMENT_STATE, ...FORWARD_RETURN_STATE },
};
