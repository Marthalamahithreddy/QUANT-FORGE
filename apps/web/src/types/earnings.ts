/**
 * Wire contracts for the read-only earnings API.
 *
 * Mirrors `quantforge.api.v1.earnings_events`. EPS and revenue are decimal
 * STRINGS and must stay strings. `pit_eligibility` and `pit_reason` are
 * computed by the backend; the frontend only displays them.
 */

import { isDecimalString } from "@/lib/format";

export type EarningsTiming =
  | "BEFORE_MARKET"
  | "DURING_MARKET"
  | "AFTER_MARKET"
  | "UNKNOWN";

export type AvailabilityBasis = "SOURCE_PUBLICATION" | "SEC_FILING_ACCEPTANCE";

export type PitEligibility =
  | "NOT_APPLICABLE"
  | "ELIGIBLE"
  | "INELIGIBLE_NO_AVAILABILITY_EVIDENCE"
  | "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS";

export type EpsSurpriseStatus =
  | "AVAILABLE"
  | "MISSING_REPORTED_EPS"
  | "MISSING_ESTIMATED_EPS"
  | "ZERO_ESTIMATE"
  | "NOT_POINT_IN_TIME_ELIGIBLE";

export interface CompanyRef {
  readonly id: string;
  readonly display_name: string;
  readonly legal_name: string;
  readonly cik: string | null;
}

export interface SourceRef {
  readonly name: string;
  readonly provider_type: string;
}

export interface ListingRef {
  readonly ticker: string;
  readonly mic: string;
  readonly exchange_name: string;
  readonly security_name: string;
  readonly security_type: string;
  readonly valid_from: string;
  readonly valid_to: string | null;
  readonly is_primary: boolean;
  readonly valid_on_event_date: boolean;
}

export interface Availability {
  readonly observed_at: string | null;
  readonly observed_at_basis: AvailabilityBasis | null;
  readonly pit_eligibility: PitEligibility;
  readonly pit_reason: string;
}

export interface EpsSurprise {
  readonly status: EpsSurpriseStatus;
  readonly absolute: string | null;
  readonly ratio: string | null;
  readonly reason: string;
}

export interface EarningsEvent {
  readonly id: string;
  readonly company: CompanyRef;
  readonly source: SourceRef;
  readonly listings: readonly ListingRef[];
  readonly fiscal_year: number;
  readonly fiscal_quarter: number | null;
  readonly event_date: string;
  readonly event_timestamp: string | null;
  readonly timing: EarningsTiming;
  readonly currency: string | null;
  readonly reported_eps: string | null;
  readonly estimated_eps: string | null;
  readonly reported_revenue: string | null;
  readonly estimated_revenue: string | null;
  readonly eps_surprise: EpsSurprise;
  readonly availability: Availability;
  readonly ingested_at: string;
}

export interface EarningsPage {
  readonly items: readonly EarningsEvent[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

export class EarningsContractError extends Error {
  constructor(reason: string) {
    super(`Unexpected payload from the QuantForge earnings API: ${reason}`);
    this.name = "EarningsContractError";
  }
}

const TIMINGS: readonly string[] = [
  "BEFORE_MARKET",
  "DURING_MARKET",
  "AFTER_MARKET",
  "UNKNOWN",
];
const BASES: readonly string[] = ["SOURCE_PUBLICATION", "SEC_FILING_ACCEPTANCE"];
const ELIGIBILITIES: readonly string[] = [
  "NOT_APPLICABLE",
  "ELIGIBLE",
  "INELIGIBLE_NO_AVAILABILITY_EVIDENCE",
  "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS",
];
const SURPRISE_STATUSES: readonly string[] = [
  "AVAILABLE",
  "MISSING_REPORTED_EPS",
  "MISSING_ESTIMATED_EPS",
  "ZERO_ESTIMATE",
  "NOT_POINT_IN_TIME_ELIGIBLE",
];

type Obj = Record<string, unknown>;

const isObj = (value: unknown): value is Obj =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isStr = (value: unknown): value is string =>
  typeof value === "string" && value !== "";
const isStrOrNull = (value: unknown): boolean => value === null || isStr(value);
const isDecimalOrNull = (value: unknown): boolean =>
  value === null || isDecimalString(value);
const isOneOf = (list: readonly string[], value: unknown): boolean =>
  typeof value === "string" && list.includes(value);

function isListing(value: unknown): value is ListingRef {
  return (
    isObj(value) &&
    isStr(value.ticker) &&
    isStr(value.mic) &&
    isStr(value.valid_from) &&
    isStrOrNull(value.valid_to) &&
    typeof value.is_primary === "boolean" &&
    typeof value.valid_on_event_date === "boolean"
  );
}

function isEvent(value: unknown): value is EarningsEvent {
  if (!isObj(value)) return false;
  const { company, source, availability, eps_surprise: surprise } = value;
  return (
    isStr(value.id) &&
    isObj(company) &&
    isStr(company.id) &&
    isStr(company.display_name) &&
    isStrOrNull(company.cik) &&
    isObj(source) &&
    isStr(source.name) &&
    Array.isArray(value.listings) &&
    value.listings.every(isListing) &&
    Number.isInteger(value.fiscal_year) &&
    (value.fiscal_quarter === null || Number.isInteger(value.fiscal_quarter)) &&
    isStr(value.event_date) &&
    isStrOrNull(value.event_timestamp) &&
    isOneOf(TIMINGS, value.timing) &&
    isStrOrNull(value.currency) &&
    isDecimalOrNull(value.reported_eps) &&
    isDecimalOrNull(value.estimated_eps) &&
    isDecimalOrNull(value.reported_revenue) &&
    isDecimalOrNull(value.estimated_revenue) &&
    isObj(surprise) &&
    isOneOf(SURPRISE_STATUSES, surprise.status) &&
    isDecimalOrNull(surprise.absolute) &&
    isDecimalOrNull(surprise.ratio) &&
    isObj(availability) &&
    isStrOrNull(availability.observed_at) &&
    (availability.observed_at_basis === null ||
      isOneOf(BASES, availability.observed_at_basis)) &&
    isOneOf(ELIGIBILITIES, availability.pit_eligibility) &&
    isStr(availability.pit_reason) &&
    isStr(value.ingested_at)
  );
}

export function parseEarningsEvent(value: unknown): EarningsEvent {
  if (!isEvent(value)) {
    throw new EarningsContractError("event does not match the documented shape.");
  }
  return value;
}

export function parseEarningsPage(value: unknown): EarningsPage {
  if (
    !isObj(value) ||
    !Array.isArray(value.items) ||
    !Number.isInteger(value.total) ||
    !Number.isInteger(value.limit) ||
    !Number.isInteger(value.offset)
  ) {
    throw new EarningsContractError("expected { items, total, limit, offset }.");
  }
  value.items.forEach(parseEarningsEvent);
  return value as unknown as EarningsPage;
}

export type DependencyState = "ok" | "unavailable";

export interface ReadinessResponse {
  readonly status: "ready" | "not_ready";
  readonly service: string;
  readonly dependencies: {
    readonly database: DependencyState;
    readonly redis: DependencyState;
  };
}

export function parseReadiness(value: unknown): ReadinessResponse {
  const states = ["ok", "unavailable"];
  if (
    !isObj(value) ||
    !isOneOf(["ready", "not_ready"], value.status) ||
    !isStr(value.service) ||
    !isObj(value.dependencies) ||
    !isOneOf(states, value.dependencies.database) ||
    !isOneOf(states, value.dependencies.redis)
  ) {
    throw new EarningsContractError("readiness payload does not match the contract.");
  }
  return value as unknown as ReadinessResponse;
}
