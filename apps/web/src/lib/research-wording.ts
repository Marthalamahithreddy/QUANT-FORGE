/**
 * Display wording for the research observation's vocabularies.
 *
 * Wording only: every status, reason and level arrives from the backend and nothing here
 * reclassifies it. The labels are neutral and conservative: no trading language, no claim about
 * when anything was public, available or tradable, and no remedy the backend did not state.
 */

import type {
  AccountingState,
  AlignmentStatus,
  BenchmarkReturnStatus,
  DisclosureEvidenceLevel,
  FilingLinkStatus,
  ForwardReturnStatus,
  ListingResolutionStatus,
  PeriodVerification,
  PointInTimeState,
  RefusalScope,
  SectionState,
} from "@/lib/api/schema";

export const SECTION_STATE_LABEL: Record<SectionState, string> = {
  AVAILABLE: "Available",
  PARTIAL: "Partial",
  UNAVAILABLE: "Unavailable",
};

export const POINT_IN_TIME_STATE_LABEL: Record<PointInTimeState, string> = {
  EVIDENCE_ELIGIBLE: "Evidence eligible",
  REFUSED: "Refused",
};

export const ALIGNMENT_LABEL: Record<AlignmentStatus, string> = {
  ALIGNED: "Aligned to stored sessions",
  UNKNOWN_TIMING: "Timing not stated",
  INTRADAY_PRECISION_UNSUPPORTED: "Intraday timing unsupported",
  NO_SESSION_DATA: "No stored sessions",
  NOT_POINT_IN_TIME_ELIGIBLE: "Withheld: not point-in-time eligible",
};

export const FORWARD_RETURN_LABEL: Record<ForwardReturnStatus, string> = {
  AVAILABLE: "Available",
  NO_ALIGNMENT: "No alignment",
  MISSING_ANCHOR_PRICE: "Missing anchor price",
  INSUFFICIENT_FORWARD_SESSIONS: "Beyond the calendar's proven horizon",
  MISSING_TARGET_PRICE: "Price not stored for target session",
  INVALID_PRICE: "Invalid price",
  AMBIGUOUS_PRICE_SOURCE: "Ambiguous price source",
};

export const BENCHMARK_RETURN_LABEL: Record<BenchmarkReturnStatus, string> = {
  AVAILABLE: "Available",
  NO_BENCHMARK: "No benchmark selected",
  NO_STOCK_RETURN: "No stock return",
  MISSING_ANCHOR_PRICE: "Missing anchor price",
  MISSING_TARGET_PRICE: "Missing target price",
  INVALID_PRICE: "Invalid price",
  AMBIGUOUS_BENCHMARK_PRICE_SOURCE: "Ambiguous price source",
};

export const FILING_LINK_LABEL: Record<FilingLinkStatus, string> = {
  NOT_LINKED: "No filing supplied",
  FILING_NOT_STORED: "Filing not stored",
  CIK_MISMATCH_REFUSED: "Registrant (CIK) mismatch",
  CALLER_SUPPLIED_CIK_CONSISTENT: "Supplied by you; registrant consistent",
};

export const ACCOUNTING_STATE_LABEL: Record<AccountingState, string> = {
  NOT_REQUESTED: "Not requested",
  LINK_REFUSED: "Link refused",
  EVALUATED: "Evaluated",
};

export const LISTING_LABEL: Record<ListingResolutionStatus, string> = {
  RESOLVED: "Resolved",
  NO_LISTING_FOR_DATE: "No listing for the event date",
  AMBIGUOUS_LISTING: "Ambiguous listing",
  CONFLICTING_HISTORY: "Conflicting listing history",
};

export const PERIOD_VERIFICATION_NOTE: Record<PeriodVerification, string | null> = {
  NOT_APPLICABLE: null,
  PERIOD_UNVERIFIED:
    "The filing's fiscal period was not checked against this event. Nothing here says the filing reports this event's quarter.",
};

export interface DisclosureWording {
  readonly label: string;
  readonly sentence: string;
}

/**
 * What each evidence level MEANS, and no more. They are categories of stored metadata: none
 * carries a time, none says the filing was public first or available to the market, and none makes
 * an observation point-in-time eligible.
 */
export const DISCLOSURE_WORDING: Record<DisclosureEvidenceLevel, DisclosureWording> = {
  NONE: { label: "None", sentence: "No filing evidence is linked to this study." },
  D0_FILING_EXISTS: { label: "D0 · a stored filing exists", sentence: "A stored filing with this accession exists." },
  D1_ITEM_2_02_DECLARED: {
    label: "D1 · Item 2.02 declared",
    sentence: "A stored 8-K filing declares Item 2.02 (Results of Operations and Financial Condition) among its items.",
  },
};

export const DISCLOSURE_CAVEAT =
  "This is a category of stored metadata. It implies no time, no first public disclosure and no availability, and it does not make the event point-in-time eligible.";

export const REFUSAL_SCOPE_LABEL: Record<RefusalScope, string> = {
  EVENT: "Event",
  MARKET: "Market",
  BENCHMARK: "Benchmark",
  FILING_LINK: "Filing link",
  ACCOUNTING: "Accounting",
  POINT_IN_TIME: "Point-in-time",
};

/** Which result section explains each scope (the ledger links to it). */
export const REFUSAL_SCOPE_ANCHOR: Record<RefusalScope, string> = {
  EVENT: "section-event",
  MARKET: "section-descriptive",
  BENCHMARK: "section-descriptive",
  FILING_LINK: "section-accounting",
  ACCOUNTING: "section-accounting",
  POINT_IN_TIME: "section-point-in-time",
};

export const DESCRIPTIVE_QUESTION = "What happened after this event.";
export const POINT_IN_TIME_QUESTION = "What could have been known and acted on, given the availability evidence.";
export const PIT_REFUSAL_SENTENCE = "This does not affect the descriptive study above.";
