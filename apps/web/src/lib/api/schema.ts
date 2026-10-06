/**
 * The backend contract as TypeScript sees it: type aliases over the GENERATED OpenAPI types plus
 * the runtime enum lists the decoders and the UI select from.
 *
 * Generated types are compile-time assistance only. They say what the backend CLAIMS; they prove
 * nothing about a received payload. Every payload is validated by a decoder (`decoders/`) before
 * it becomes one of these types, and `tests/openapi-contract.test.ts` proves each runtime enum
 * list below is exactly the snapshot's list, so a backend enum change fails a test instead of
 * being rendered as a generic "unknown".
 */

import type { components } from "@/types/generated/api";

type Schemas = components["schemas"];

// ── response shapes ──────────────────────────────────────────────────────────────────────

export type HealthResponse = Schemas["HealthResponse"];
export type ReadinessResponse = Schemas["ReadinessResponse"];
export type DependencyState = Schemas["DependencyReadiness"]["database"];

export type EarningsEvent = Schemas["EarningsEventItem"];
export type EarningsPage = Schemas["EarningsEventPage"];
export type CompanyRef = Schemas["CompanyRef"];
export type SourceRef = Schemas["SourceRef"];
export type ListingRef = Schemas["ListingRef"];
export type Availability = Schemas["Availability"];
export type EpsSurprise = Schemas["EpsSurprise"];

export type FilingIndexItem = Schemas["FilingIndexItem"];
export type FilingIndexPage = Schemas["FilingIndexPage"];
export type FilingEps = Schemas["FilingEpsResponse"];
export type FactEvidence = Schemas["FactEvidence"];
export type CurrentEps = Schemas["CurrentEpsSection"];
export type ComparativeEps = Schemas["ComparativeEpsSection"];
export type EpsChange = Schemas["EpsChangeSection"];

// ── enums (exhaustive at compile time, equal to the snapshot at test time) ──────────────

/**
 * Declares a runtime list for the backend enum `T`. The list must contain every member (TypeScript
 * rejects a missing one) and only members (it rejects an invented one).
 */
function enumList<T extends string>() {
  return <const L extends readonly T[]>(
    list: L & ([Exclude<T, L[number]>] extends [never] ? unknown : never),
  ): L => list;
}

export const FILING_FORMS = enumList<Schemas["SecFormType"]>()([
  "10-K",
  "10-K/A",
  "10-Q",
  "10-Q/A",
  "8-K",
  "8-K/A",
]);
export type FilingForm = Schemas["SecFormType"];

export const PERIOD_CLASSES = enumList<Schemas["EpsPeriodClass"]>()(["QUARTER", "ANNUAL"]);
export type EpsPeriodClass = Schemas["EpsPeriodClass"];

export const EPS_KINDS = enumList<Schemas["EpsKind"]>()(["DILUTED", "BASIC_AND_DILUTED"]);
export type EpsKind = Schemas["EpsKind"];

export const EPS_STATUSES = enumList<Schemas["EpsStatus"]>()([
  "AVAILABLE",
  "NOT_INGESTED",
  "UNSUPPORTED_FORM_PERIOD",
  "MISSING",
  "EXTENSION_ONLY",
  "WRONG_UNIT",
  "DIMENSIONAL_ONLY",
  "COREG_ONLY",
  "NULL_VALUE",
  "AMBIGUOUS",
]);
export type EpsStatus = Schemas["EpsStatus"];

export const COMPARATIVE_STATUSES = enumList<Schemas["ComparativeEpsStatus"]>()([
  "AVAILABLE",
  "CURRENT_UNAVAILABLE",
  "COMPARATIVE_MISSING",
  "COMPARATIVE_PERIOD_MISMATCH",
  "COMPARATIVE_DIMENSIONAL_ONLY",
  "COMPARATIVE_COREG_ONLY",
  "COMPARATIVE_WRONG_UNIT",
  "COMPARATIVE_NULL_VALUE",
  "COMPARATIVE_AMBIGUOUS",
]);
export type ComparativeStatus = Schemas["ComparativeEpsStatus"];

export const CHANGE_STATUSES = enumList<Schemas["EpsChangeStatus"]>()([
  "AVAILABLE",
  "INPUT_UNAVAILABLE",
]);
export type ChangeStatus = Schemas["EpsChangeStatus"];

export const TRANSITIONS = enumList<Schemas["EpsTransition"]>()([
  "POSITIVE_INCREASED",
  "POSITIVE_DECREASED",
  "POSITIVE_UNCHANGED",
  "LOSS_NARROWED",
  "LOSS_WIDENED",
  "LOSS_UNCHANGED",
  "LOSS_TO_PROFIT",
  "PROFIT_TO_LOSS",
  "ZERO_TO_PROFIT",
  "ZERO_TO_LOSS",
  "PROFIT_TO_ZERO",
  "LOSS_TO_ZERO",
  "ZERO_UNCHANGED",
]);
export type Transition = Schemas["EpsTransition"];

export const PERCENTAGE_REASONS = enumList<Schemas["PercentageUnavailableReason"]>()([
  "ZERO_COMPARATIVE",
  "NEGATIVE_COMPARATIVE",
]);
export type PercentageReason = Schemas["PercentageUnavailableReason"];

export const EARNINGS_TIMINGS = enumList<Schemas["EarningsTiming"]>()([
  "BEFORE_MARKET",
  "DURING_MARKET",
  "AFTER_MARKET",
  "UNKNOWN",
]);
export type EarningsTiming = Schemas["EarningsTiming"];

export const AVAILABILITY_BASES = enumList<Schemas["AvailabilityBasis"]>()([
  "SOURCE_PUBLICATION",
  "SEC_FILING_ACCEPTANCE",
]);
export type AvailabilityBasis = Schemas["AvailabilityBasis"];

export const PIT_ELIGIBILITIES = enumList<Schemas["PointInTimeEligibility"]>()([
  "NOT_APPLICABLE",
  "ELIGIBLE",
  "INELIGIBLE_NO_AVAILABILITY_EVIDENCE",
  "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS",
]);
export type PitEligibility = Schemas["PointInTimeEligibility"];

export const EPS_SURPRISE_STATUSES = enumList<Schemas["EpsSurpriseStatus"]>()([
  "AVAILABLE",
  "MISSING_REPORTED_EPS",
  "MISSING_ESTIMATED_EPS",
  "ZERO_ESTIMATE",
  "NOT_POINT_IN_TIME_ELIGIBLE",
]);
export type EpsSurpriseStatus = Schemas["EpsSurpriseStatus"];

export const PROVIDER_TYPES = enumList<Schemas["ProviderType"]>()([
  "REGULATORY_FILING",
  "EXCHANGE_FEED",
  "MARKET_DATA_VENDOR",
  "BROKER_API",
  "MANUAL",
  "OTHER",
]);
export type ProviderType = Schemas["ProviderType"];

export const SECURITY_TYPES = enumList<Schemas["SecurityType"]>()([
  "EQUITY",
  "PREFERRED_EQUITY",
  "DEPOSITORY_RECEIPT",
  "ETF",
  "MUTUAL_FUND",
  "BOND",
  "OTHER",
]);
export type SecurityType = Schemas["SecurityType"];

export const READINESS_STATUSES = ["ready", "not_ready"] as const;
export const DEPENDENCY_STATES = ["ok", "unavailable"] as const;

// ── list-page bounds (mirrors the API's parameter limits; parity-tested) ─────────────────

/** `limit` default and maximum of every paginated endpoint. */
export const PAGE_SIZE = 50;
export const PAGE_LIMIT_MAX = 200;
/** `offset` maximum of every paginated endpoint. */
export const PAGE_OFFSET_MAX = 1_000_000;

/** Backend patterns shared by the decoders and the URL parsers. */
export const ACCESSION_PATTERN = /^\d{10}-\d{2}-\d{6}$/;
export const CIK_PATTERN = /^\d{10}$/;
