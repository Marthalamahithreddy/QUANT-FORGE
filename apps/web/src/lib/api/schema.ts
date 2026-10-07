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

export type BenchmarkItem = Schemas["BenchmarkIndexItem"];
export type BenchmarkPage = Schemas["BenchmarkIndexPage"];

export type Observation = Schemas["ResearchObservationResponse"];
export type ObservationIdentity = Schemas["IdentityOut"];
export type ListingResolution = Schemas["ListingResolutionOut"];
export type ObservationParameters = Schemas["ObservationParametersOut"];
export type ObservationEvent = Schemas["EventOut"];
export type FilingLink = Schemas["FilingLinkOut"];
export type DisclosureEvidence = Schemas["DisclosureEvidenceOut"];
export type AccountingSection = Schemas["AccountingOut"];
export type DescriptiveSection = Schemas["DescriptiveOut"];
export type PointInTimeSection = Schemas["PointInTimeOut"];
export type Study = Schemas["StudyOut"];
export type Alignment = Schemas["AlignmentOut"];
export type SessionAlignment = Schemas["SessionAlignmentOut"];
export type StockReturns = Schemas["StockReturnsOut"];
export type ForwardReturn = Schemas["ForwardReturnOut"];
export type BenchmarkSection = Schemas["BenchmarkOut"];
export type BenchmarkReturn = Schemas["BenchmarkReturnOut"];
export type AbnormalReturn = Schemas["AbnormalReturnOut"];
export type ObservationEpsSurprise = Schemas["EpsSurpriseOut"];
export type ObservationProvenance = Schemas["ProvenanceOut"];
export type ArtifactDigest = Schemas["ArtifactDigestOut"];
export type Refusal = Schemas["RefusalOut"];
export type CurrentEps = Schemas["CurrentEpsSection"];
export type ComparativeEps = Schemas["ComparativeEpsSection"];
export type EpsChange = Schemas["EpsChangeSection"];

/** The stored request. The API types the two selectors as plain strings; they are the enums below. */
export type RecordRequest = Omit<Schemas["RecordRequestSummary"], "eps_kind" | "eps_period_class"> & {
  readonly eps_kind: Schemas["EpsKind"] | null;
  readonly eps_period_class: Schemas["EpsPeriodClass"] | null;
};
export type RecordSummary = Omit<Schemas["ResearchRecordSummary"], "request"> & { readonly request: RecordRequest };
export type RecordPage = Omit<Schemas["ResearchRecordPage"], "items"> & { readonly items: RecordSummary[] };
export type RecordFingerprints = Schemas["RecordFingerprints"];
export type Verification = Schemas["RecordVerificationResponse"];
export type VerificationSide = Schemas["RecordVerificationSide"];

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

// ── research observation vocabulary ───────────────────────────────────────────────────────

export const SECTION_STATES = enumList<Schemas["SectionState"]>()(["AVAILABLE", "PARTIAL", "UNAVAILABLE"]);
export type SectionState = Schemas["SectionState"];

export const POINT_IN_TIME_STATES = enumList<Schemas["PointInTimeState"]>()(["REFUSED", "EVIDENCE_ELIGIBLE"]);
export type PointInTimeState = Schemas["PointInTimeState"];

export const RESEARCH_MODES = enumList<Schemas["ResearchMode"]>()(["DESCRIPTIVE", "POINT_IN_TIME"]);
export type ResearchMode = Schemas["ResearchMode"];

export const PRICE_BASES = enumList<Schemas["PriceBasis"]>()(["SPLIT_ADJUSTED", "AS_TRADED"]);
export type PriceBasis = Schemas["PriceBasis"];

export const ALIGNMENT_STATUSES = enumList<Schemas["AlignmentStatus"]>()([
  "ALIGNED",
  "UNKNOWN_TIMING",
  "INTRADAY_PRECISION_UNSUPPORTED",
  "NO_SESSION_DATA",
  "NOT_POINT_IN_TIME_ELIGIBLE",
]);
export type AlignmentStatus = Schemas["AlignmentStatus"];

export const FORWARD_RETURN_STATUSES = enumList<Schemas["ForwardReturnStatus"]>()([
  "AVAILABLE",
  "NO_ALIGNMENT",
  "MISSING_ANCHOR_PRICE",
  "INSUFFICIENT_FORWARD_SESSIONS",
  "INVALID_PRICE",
  "AMBIGUOUS_PRICE_SOURCE",
]);
export type ForwardReturnStatus = Schemas["ForwardReturnStatus"];

export const BENCHMARK_RETURN_STATUSES = enumList<Schemas["BenchmarkReturnStatus"]>()([
  "AVAILABLE",
  "NO_BENCHMARK",
  "NO_STOCK_RETURN",
  "MISSING_ANCHOR_PRICE",
  "MISSING_TARGET_PRICE",
  "INVALID_PRICE",
  "AMBIGUOUS_BENCHMARK_PRICE_SOURCE",
]);
export type BenchmarkReturnStatus = Schemas["BenchmarkReturnStatus"];

export const ABNORMAL_RETURN_STATUSES = enumList<Schemas["AbnormalReturnStatus"]>()([
  "AVAILABLE",
  "STOCK_RETURN_UNAVAILABLE",
  "BENCHMARK_RETURN_UNAVAILABLE",
]);
export type AbnormalReturnStatus = Schemas["AbnormalReturnStatus"];

export const FILING_LINK_STATUSES = enumList<Schemas["FilingLinkStatus"]>()([
  "NOT_LINKED",
  "FILING_NOT_STORED",
  "CIK_MISMATCH_REFUSED",
  "CALLER_SUPPLIED_CIK_CONSISTENT",
]);
export type FilingLinkStatus = Schemas["FilingLinkStatus"];

export const PERIOD_VERIFICATIONS = enumList<Schemas["PeriodVerification"]>()(["NOT_APPLICABLE", "PERIOD_UNVERIFIED"]);
export type PeriodVerification = Schemas["PeriodVerification"];

export const ACCOUNTING_STATES = enumList<Schemas["AccountingState"]>()(["NOT_REQUESTED", "LINK_REFUSED", "EVALUATED"]);
export type AccountingState = Schemas["AccountingState"];

export const DISCLOSURE_LEVELS = enumList<Schemas["DisclosureEvidenceLevel"]>()([
  "NONE",
  "D0_FILING_EXISTS",
  "D1_ITEM_2_02_DECLARED",
]);
export type DisclosureEvidenceLevel = Schemas["DisclosureEvidenceLevel"];

export const REFUSAL_SCOPES = enumList<Schemas["RefusalScope"]>()([
  "EVENT",
  "MARKET",
  "BENCHMARK",
  "FILING_LINK",
  "ACCOUNTING",
  "POINT_IN_TIME",
]);
export type RefusalScope = Schemas["RefusalScope"];

export const LISTING_RESOLUTION_STATUSES = enumList<Schemas["ListingResolutionStatus"]>()([
  "RESOLVED",
  "NO_LISTING_FOR_DATE",
  "AMBIGUOUS_LISTING",
  "CONFLICTING_HISTORY",
]);
export type ListingResolutionStatus = Schemas["ListingResolutionStatus"];

export const LISTING_EVIDENCE = enumList<Schemas["Evidence"]>()(["PROVISIONAL_FIRST_OBSERVED", "SOURCE_STATED_HISTORY"]);

export const ARTIFACT_ROLES = ["current", "comparative"] as const;

// ── saved research records ────────────────────────────────────────────────────────────────

export const REPRODUCIBILITY_GAPS = enumList<Schemas["ReproducibilityGap"]>()([
  "EVENT_WITHOUT_INGESTION_RUN",
  "STOCK_BAR_WITHOUT_INGESTION_RUN",
  "BENCHMARK_BARS_WITHOUT_INGESTION_RUN",
  "FILING_WITHOUT_INGESTION_RUN",
  "ACCOUNTING_NOT_INGESTED",
]);
export type ReproducibilityGap = Schemas["ReproducibilityGap"];

export const VERIFICATION_STATUSES = enumList<Verification["status"]>()([
  "MATCH",
  "MANIFEST_CHANGED",
  "RESULT_CHANGED",
  "EVIDENCE_INCOMPLETE",
]);
export type VerificationStatus = Verification["status"];

export const VERIFICATION_BASELINES = enumList<Verification["baseline"]>()(["SUPPLIED", "RERUN"]);

export const MANIFEST_SECTIONS = enumList<Verification["changed_sections"][number]>()(["request", "methodology", "evidence"]);
export type ManifestSection = Verification["changed_sections"][number];

/** The one fingerprint algorithm the record contract defines. */
export const HASH_ALGORITHM = "sha256";

// ── research request bounds (parity-tested against the observation parameters) ───────────

/** The observation endpoint's horizon rules: at most 10 horizons, each from 1 to 252 sessions. */
export const HORIZON_MAX_COUNT = 10;
export const HORIZON_MAX_VALUE = 252;
/** Used when `horizons` is absent; echoed ascending in `parameters.horizons`. */
export const DEFAULT_HORIZONS = [1, 5, 20] as const;

export const READINESS_STATUSES = ["ready", "not_ready"] as const;
export const DEPENDENCY_STATES = ["ok", "unavailable"] as const;

// ── list-page bounds (mirrors the API's parameter limits; parity-tested) ─────────────────

/** `limit` default and maximum of every paginated endpoint. */
export const PAGE_SIZE = 50;
export const PAGE_LIMIT_MAX = 200;
/** Rows per page of the saved-research list (the API default is 50; a page of 20 keeps rows scannable). */
export const RECORD_PAGE_SIZE = 20;
/** `offset` maximum of every paginated endpoint. */
export const PAGE_OFFSET_MAX = 1_000_000;

/** Backend patterns shared by the decoders and the URL parsers. */
export const ACCESSION_PATTERN = /^\d{10}-\d{2}-\d{6}$/;
export const CIK_PATTERN = /^\d{10}$/;
