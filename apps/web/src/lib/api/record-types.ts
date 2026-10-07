/**
 * The payload of a saved research record: the stored MANIFEST (what was asked, how it was computed,
 * which evidence it consumed) and the stored RESULT (what came out).
 *
 * Why these are written by hand and not generated: the OpenAPI snapshot declares both as open
 * objects (`additionalProperties: true`, "parsed"), so the generated types say nothing about their
 * contents. Their shape is the backend's canonical serialisation of its own domain objects, which
 * is NOT the shape of the live observation response (it has no presenter-added fields, and its
 * per-horizon lists are nested differently). The runtime decoder (`decoders/records.ts`) is
 * therefore the authority for these shapes, and the captured backend fixtures
 * (`tests/contract/records.json`) are what pin them.
 *
 * Every financial value is an exact decimal STRING. Nothing here is ever a JS number except counts
 * and horizons.
 */

import type {
  AbnormalReturn,
  AccountingState,
  Alignment,
  AvailabilityBasis,
  BenchmarkReturn,
  ChangeStatus,
  ComparativeStatus,
  DisclosureEvidence,
  EarningsTiming,
  EpsKind,
  EpsPeriodClass,
  EpsStatus,
  FilingForm,
  FilingLink,
  ListingResolution,
  ListingResolutionStatus,
  ObservationEpsSurprise,
  ObservationEvent,
  PercentageReason,
  PitEligibility,
  PriceBasis,
  RecordSummary,
  ResearchMode,
  StockReturns,
  Transition,
} from "@/lib/api/schema";

/**
 * How a piece of evidence stands in a manifest. Not in the OpenAPI snapshot (the manifest is an
 * open object), so this list is pinned by the decoder and the captured fixtures only.
 */
export const EVIDENCE_STATES = ["NOT_REQUESTED", "NOT_READ", "PINNED"] as const;
export type EvidenceState = (typeof EVIDENCE_STATES)[number];

// ── manifest ─────────────────────────────────────────────────────────────────────────────

export interface ManifestRequest {
  readonly event_id: string;
  readonly horizons: readonly number[];
  readonly benchmark_id: string | null;
  readonly benchmark_source_id: string | null;
  readonly security_id: string | null;
  readonly stock_source_id: string | null;
  readonly price_basis: PriceBasis;
  readonly filing_accession: string | null;
  readonly eps_period_class: EpsPeriodClass | null;
  readonly eps_kind: EpsKind | null;
  readonly modes: readonly ResearchMode[];
}

export interface ManifestMethodology {
  /** `[component, version label]`, sorted by component. */
  readonly components: readonly (readonly [string, string])[];
  readonly approved_availability_bases: readonly AvailabilityBasis[];
  readonly alignment_window_days: number;
}

export interface BarPin {
  readonly session: string;
  readonly bar_id: string;
  readonly source_id: string;
  /** Exact decimal string. */
  readonly close: string;
  readonly ingested_at: string;
  readonly ingestion_run_id: string | null;
}

export interface SessionsPin {
  readonly window_start: string;
  readonly window_end: string;
  readonly session_count: number;
  readonly sessions_sha256: string;
}

export interface EventEvidenceManifest {
  readonly event_id: string;
  readonly fingerprint: string;
  readonly source_id: string;
  readonly ingestion_run_id: string | null;
  readonly event_date: string;
  readonly timing: EarningsTiming;
}

export interface PitEvidenceManifest {
  readonly eligibility: PitEligibility;
  readonly observed_at: string | null;
  readonly observed_at_basis: AvailabilityBasis | null;
}

export interface StockEvidenceManifest {
  readonly state: EvidenceState;
  readonly listing_id: string | null;
  readonly security_id: string | null;
  readonly price_basis: PriceBasis;
  readonly sessions: SessionsPin | null;
  readonly price_source_id: string | null;
  readonly bars: readonly BarPin[];
}

export interface BenchmarkEvidenceManifest {
  readonly state: EvidenceState;
  readonly benchmark_id: string | null;
  readonly price_basis: PriceBasis;
  readonly window_start: string | null;
  readonly window_end: string | null;
  readonly session_count: number | null;
  readonly sessions_sha256: string | null;
  readonly bars: readonly BarPin[];
}

export interface FilingEvidenceManifest {
  readonly state: EvidenceState;
  readonly accession_number: string | null;
  readonly form: FilingForm | null;
  readonly source_id: string | null;
  readonly ingestion_run_id: string | null;
  /** Stored 8-K item codes, verbatim. Not a time. */
  readonly items: string | null;
  readonly disclosure_level: DisclosureEvidence["level"];
}

export interface FactPin {
  readonly role: "current" | "comparative";
  readonly fact_id: string;
  readonly tag: string;
  readonly taxonomy_version: string;
  readonly unit: string;
  readonly period_end: string;
  readonly qtrs: number;
  /** Exact decimal string, or null when the source stated none. */
  readonly value: string | null;
  readonly segments: string;
  readonly coreg: string;
  readonly artifact_sha256: string;
}

export interface AccountingEvidenceManifest {
  readonly state: EvidenceState;
  /** `[package name, sha256]`. */
  readonly artifacts: readonly (readonly [string, string])[];
  readonly facts: readonly FactPin[];
}

export interface ManifestEvidence {
  readonly event: EventEvidenceManifest;
  readonly listing_resolution_status: ListingResolutionStatus;
  readonly listing_evidence: ListingResolution["evidence"];
  readonly pit: PitEvidenceManifest;
  readonly stock: StockEvidenceManifest;
  readonly benchmark: BenchmarkEvidenceManifest;
  readonly filing: FilingEvidenceManifest;
  readonly accounting: AccountingEvidenceManifest;
}

export interface StoredManifest {
  readonly request: ManifestRequest;
  readonly methodology: ManifestMethodology;
  readonly evidence: ManifestEvidence;
}

// ── result ───────────────────────────────────────────────────────────────────────────────

/** One stored study (descriptive, or point-in-time when eligible), in the backend's stored shape. */
export interface StoredStudy {
  readonly research_mode: ResearchMode;
  readonly point_in_time_eligibility: PitEligibility;
  readonly eps_surprise: ObservationEpsSurprise;
  readonly alignment: Alignment;
  readonly stock_returns: StockReturns;
  readonly benchmark_id: string | null;
  readonly benchmark_returns: readonly BenchmarkReturn[];
  readonly abnormal_returns: readonly AbnormalReturn[];
  readonly price_basis_note: string;
}

export interface StoredFact {
  readonly fact_id: string;
  readonly tag: string;
  readonly taxonomy_version: string;
  readonly unit: string;
  readonly period_end: string;
  readonly duration_quarters: number;
  readonly value: string | null;
  readonly segments: string;
  readonly coreg: string;
}

export interface StoredCurrentEps {
  readonly status: EpsStatus;
  readonly reason: string;
  readonly fact_id: string | null;
  readonly tag: string | null;
  readonly taxonomy_version: string | null;
  readonly unit: string | null;
  readonly period_end: string | null;
  readonly duration_quarters: number | null;
  readonly value: string | null;
  readonly segments: string | null;
  readonly coreg: string | null;
  readonly accession_number: string;
  readonly company_cik: string | null;
  readonly form: FilingForm | null;
  readonly period_class: EpsPeriodClass;
  readonly eps_kind: EpsKind;
}

export interface StoredComparativeEps {
  readonly status: ComparativeStatus;
  readonly reason: string;
  readonly comparative_fact: StoredFact | null;
  readonly accession_number: string;
  readonly company_cik: string | null;
  readonly form: FilingForm | null;
  readonly period_class: EpsPeriodClass;
  readonly eps_kind: EpsKind;
  readonly current: StoredCurrentEps;
}

export interface StoredEpsChange {
  readonly status: ChangeStatus;
  /** Exact decimal strings, as stored. */
  readonly eps_change: string | null;
  readonly percentage_change: string | null;
  readonly percentage_unavailable_reason: PercentageReason | null;
  readonly transition: Transition | null;
  readonly comparative: StoredComparativeEps;
}

export interface StoredAccounting {
  readonly state: AccountingState;
  /** Present exactly when the stored accounting was evaluated. */
  readonly change: StoredEpsChange | null;
}

export interface StoredIdentity {
  readonly company_id: string;
  readonly company_cik: string | null;
  readonly event_id: string;
  readonly listing_resolution: ListingResolution;
  readonly benchmark_id: string | null;
  readonly filing_accession: string | null;
}

export interface StoredPointInTime {
  readonly eligibility: PitEligibility;
  /** Present exactly when the stored evidence was eligible. */
  readonly study: StoredStudy | null;
}

export interface StoredResult {
  readonly event: ObservationEvent;
  readonly identity: StoredIdentity;
  readonly filing_link: FilingLink;
  readonly disclosure_evidence: DisclosureEvidence;
  readonly accounting: StoredAccounting;
  readonly descriptive: { readonly study: StoredStudy };
  readonly point_in_time: StoredPointInTime;
}

/**
 * `GET /research/records/{id}`: the summary fields plus the stored manifest and result, parsed. The
 * API serves it only after the backend has re-validated the record's own integrity.
 */
export interface RecordDetail extends RecordSummary {
  readonly manifest: StoredManifest;
  readonly result: StoredResult;
}
