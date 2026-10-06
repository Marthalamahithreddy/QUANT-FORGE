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
  ChangeStatus,
  ComparativeStatus,
  EarningsTiming,
  EpsStatus,
  EpsSurpriseStatus,
  PercentageReason,
  PitEligibility,
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
