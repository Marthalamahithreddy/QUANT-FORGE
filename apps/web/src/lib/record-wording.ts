/**
 * Display wording for saved research records and their verification.
 *
 * Wording only: the level, the gaps and the verification status arrive from the backend and
 * nothing here reclassifies them. The sentences say what each term means in the backend's own
 * semantics and no more: PARTIAL is a statement about the evidence trail (not the result's
 * correctness), a MATCH is about the stored evidence (not about the world), and a verification
 * never changes the saved record.
 */

import type { EvidenceState } from "@/lib/api/record-types";
import type { ManifestSection, ReproducibilityGap, VerificationStatus } from "@/lib/api/schema";

export const GAP_LABEL: Record<ReproducibilityGap, string> = {
  EVENT_WITHOUT_INGESTION_RUN: "Event",
  STOCK_BAR_WITHOUT_INGESTION_RUN: "Stock price bars",
  BENCHMARK_BARS_WITHOUT_INGESTION_RUN: "Benchmark levels",
  FILING_WITHOUT_INGESTION_RUN: "Filing",
  ACCOUNTING_NOT_INGESTED: "Accounting package",
};

export const GAP_SENTENCE: Record<ReproducibilityGap, string> = {
  EVENT_WITHOUT_INGESTION_RUN:
    "The stored earnings event has no recorded ingestion run, so the load that produced it cannot be identified.",
  STOCK_BAR_WITHOUT_INGESTION_RUN:
    "At least one stock price bar this study consumed has no recorded ingestion run.",
  BENCHMARK_BARS_WITHOUT_INGESTION_RUN:
    "At least one benchmark level this study consumed has no recorded ingestion run.",
  FILING_WITHOUT_INGESTION_RUN:
    "The filing this study consumed has no recorded ingestion run.",
  ACCOUNTING_NOT_INGESTED:
    "A filing was supplied for accounting, but no FSDS package carrying its facts is stored, so the accounting evidence cannot be pinned to an artifact.",
};

/** A concise, human list of the gaps for a table cell ("Event, Stock price bars"). */
export const gapSummary = (gaps: readonly ReproducibilityGap[]): string => gaps.map((gap) => GAP_LABEL[gap]).join(", ");

export const VERIFICATION_LABEL: Record<VerificationStatus, string> = {
  MATCH: "Match",
  EVIDENCE_INCOMPLETE: "Evidence incomplete",
  MANIFEST_CHANGED: "Manifest changed",
  RESULT_CHANGED: "Result changed",
};

/** What the verifier's status means, in the backend's semantics. */
export const VERIFICATION_SENTENCE: Record<VerificationStatus, string> = {
  MATCH:
    "Re-running this request against the evidence stored now reproduces the stored baseline: same manifest and same result. This shows the currently stored evidence yields the same output. It does not show the result is correct, and it does not show that source data outside the stored evidence still exists.",
  EVIDENCE_INCOMPLETE:
    "The manifest and the result still match the stored baseline, but the baseline or the rerun has incomplete evidence provenance (reproducibility PARTIAL). A match on evidence that is not fully pinned is not a complete reproducibility claim. This is not a change in the result.",
  MANIFEST_CHANGED:
    "The rerun's effective request, methodology or consumed evidence differs from the stored baseline's manifest. The changed sections and paths below are exactly as the verifier reported them. The result paths that differ are listed separately.",
  RESULT_CHANGED:
    "The manifest is unchanged, but the rerun produces a different result from the stored baseline. The differing result paths below are exactly as the verifier reported them.",
};

export const VERIFICATION_NOTE =
  "Verification compares a re-run with this stored baseline. It does not change, replace or refresh the saved record.";

export const SECTION_LABEL: Record<ManifestSection, string> = {
  request: "Request",
  methodology: "Methodology",
  evidence: "Evidence",
};

export const EVIDENCE_STATE_LABEL: Record<EvidenceState, string> = {
  NOT_REQUESTED: "Not requested",
  NOT_READ: "Requested, not read",
  PINNED: "Pinned",
};

export const EVIDENCE_STATE_SENTENCE: Record<EvidenceState, string> = {
  NOT_REQUESTED: "This evidence was not asked for.",
  NOT_READ: "This evidence was asked for, but an upstream refusal meant nothing was read.",
  PINNED: "This evidence was read; the identifiers below describe what the study consumed.",
};

export const BASELINE_SENTENCE =
  "A stored baseline: the historical output of one research computation, as it was persisted. It is immutable and does not change when the underlying data does.";

export const CREATED_AT_NOTE =
  "When the baseline was saved. It is persistence metadata: not the event time, the evidence time, a provider's availability time or a filing time.";

export const FINGERPRINT_NOTE: Record<"manifest" | "result", string> = {
  manifest: "Identity of the request, methodology and consumed evidence. It is not the identity of the record.",
  result: "Identity of the stored result: the computed output.",
};

/** The first characters of a hex digest, for dense tables. The full value is always kept beside it. */
export const SHORT_FINGERPRINT_LENGTH = 12;
export const shortFingerprint = (fingerprint: string): string => fingerprint.slice(0, SHORT_FINGERPRINT_LENGTH);
export const SHORT_ID_LENGTH = 8;
export const shortId = (id: string): string => id.slice(0, SHORT_ID_LENGTH);
