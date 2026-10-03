/**
 * Display wording for availability and timing enums.
 *
 * Wording only. Whether an event is point-in-time eligible is decided by the
 * backend and arrives as `pit_eligibility`; nothing here recomputes it.
 */

import type {
  AvailabilityBasis,
  EarningsTiming,
  PitEligibility,
} from "@/types/earnings";

export const BASIS_LABEL: Record<AvailabilityBasis, string> = {
  SOURCE_PUBLICATION: "Source publication",
  SEC_FILING_ACCEPTANCE: "SEC filing acceptance",
};

/** What the timestamp does and does not prove, per basis. */
export const BASIS_MEANING: Record<AvailabilityBasis, string> = {
  SOURCE_PUBLICATION: "The source states when this observation was published.",
  SEC_FILING_ACCEPTANCE:
    "The time the SEC accepted the filing on EDGAR. It is not the earnings release time and does not prove first public disclosure.",
};

export const TIMING_LABEL: Record<EarningsTiming, string> = {
  BEFORE_MARKET: "Before open",
  DURING_MARKET: "During session",
  AFTER_MARKET: "After close",
  UNKNOWN: "Not stated",
};

export type Tone = "neutral" | "positive" | "caution" | "negative";

export interface PitPresentation {
  readonly label: string;
  readonly short: string;
  readonly tone: Tone;
}

export const PIT_PRESENTATION: Record<PitEligibility, PitPresentation> = {
  ELIGIBLE: { label: "PIT eligible", short: "Approved evidence", tone: "positive" },
  INELIGIBLE_NO_AVAILABILITY_EVIDENCE: {
    label: "Not PIT eligible",
    short: "No availability evidence",
    tone: "caution",
  },
  INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS: {
    label: "Not PIT eligible",
    short: "Basis not approved",
    tone: "caution",
  },
  NOT_APPLICABLE: { label: "Descriptive", short: "Not applicable", tone: "neutral" },
};
