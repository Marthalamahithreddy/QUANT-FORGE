/**
 * Wire contracts for the read-only filings APIs.
 *
 * Mirrors `quantforge.schemas.filing_index` (`GET /api/v1/filings`) and
 * `quantforge.schemas.filing_eps` (`GET /api/v1/filings/{accession}/eps`).
 *
 * Every financial figure is a decimal STRING and stays a string end to end: nothing here, or
 * downstream, turns one into a JS number. Statuses, transitions and reasons are the backend's own
 * classification; the frontend only displays them. Every enum is validated at runtime, and an
 * unknown value is a contract failure, never silently mapped to a generic state.
 */

import { isDecimalString } from "@/lib/format";

// ── enums ────────────────────────────────────────────────────────────────────────────────

export const FILING_FORMS = [
  "10-K",
  "10-K/A",
  "10-Q",
  "10-Q/A",
  "8-K",
  "8-K/A",
] as const;
export type FilingForm = (typeof FILING_FORMS)[number];

export const PERIOD_CLASSES = ["QUARTER", "ANNUAL"] as const;
export type EpsPeriodClass = (typeof PERIOD_CLASSES)[number];

export const EPS_KINDS = ["DILUTED", "BASIC_AND_DILUTED"] as const;
export type EpsKind = (typeof EPS_KINDS)[number];

export const EPS_STATUSES = [
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
] as const;
export type EpsStatus = (typeof EPS_STATUSES)[number];

export const COMPARATIVE_STATUSES = [
  "AVAILABLE",
  "CURRENT_UNAVAILABLE",
  "COMPARATIVE_MISSING",
  "COMPARATIVE_PERIOD_MISMATCH",
  "COMPARATIVE_DIMENSIONAL_ONLY",
  "COMPARATIVE_COREG_ONLY",
  "COMPARATIVE_WRONG_UNIT",
  "COMPARATIVE_NULL_VALUE",
  "COMPARATIVE_AMBIGUOUS",
] as const;
export type ComparativeStatus = (typeof COMPARATIVE_STATUSES)[number];

export const CHANGE_STATUSES = ["AVAILABLE", "INPUT_UNAVAILABLE"] as const;
export type ChangeStatus = (typeof CHANGE_STATUSES)[number];

export const TRANSITIONS = [
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
] as const;
export type Transition = (typeof TRANSITIONS)[number];

export const PERCENTAGE_REASONS = [
  "ZERO_COMPARATIVE",
  "NEGATIVE_COMPARATIVE",
] as const;
export type PercentageReason = (typeof PERCENTAGE_REASONS)[number];

// ── filings index ────────────────────────────────────────────────────────────────────────

export interface FilingIndexItem {
  readonly accession: string;
  readonly company_cik: string | null;
  readonly form: FilingForm;
  readonly filing_date: string;
  readonly report_date: string | null;
  readonly fsds_period_end: string | null;
}

export interface FilingIndexPage {
  readonly items: readonly FilingIndexItem[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

// ── filing EPS ───────────────────────────────────────────────────────────────────────────

export interface FactEvidence {
  readonly fact_id: string;
  readonly tag: string;
  readonly taxonomy_version: string;
  readonly unit: string;
  readonly period_end: string;
  readonly qtrs: number;
  /** Exact decimal string, or `null` when the source stated none (never zero). */
  readonly value: string | null;
  readonly segments: string;
  readonly coreg: string;
}

export interface CurrentEps {
  readonly status: EpsStatus;
  readonly reason: string;
  readonly fact: FactEvidence | null;
}

export interface ComparativeEps {
  readonly status: ComparativeStatus;
  readonly reason: string;
  readonly meaning: string;
  readonly fact: FactEvidence | null;
}

export interface EpsChange {
  readonly status: ChangeStatus;
  readonly eps_change: string | null;
  readonly transition: Transition | null;
  /** A RATIO string (0.5 = +50%), never a percentage. */
  readonly percentage_change: string | null;
  readonly percentage_unavailable_reason: PercentageReason | null;
}

export interface FilingEps {
  readonly accession: string;
  readonly company_cik: string | null;
  readonly form: FilingForm | null;
  readonly period_class: EpsPeriodClass;
  readonly eps_kind: EpsKind;
  readonly current: CurrentEps;
  readonly comparative: ComparativeEps;
  readonly change: EpsChange;
}

// ── runtime validation ───────────────────────────────────────────────────────────────────

export class FilingsContractError extends Error {
  constructor(reason: string) {
    super(`Unsupported payload from the QuantForge filings API: ${reason}`);
    this.name = "FilingsContractError";
  }
}

type Obj = Record<string, unknown>;

const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;
const CIK = /^\d{10}$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

const isObj = (value: unknown): value is Obj =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string =>
  typeof value === "string" && value !== "";
const isNonNegativeInt = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;
const isDate = (value: unknown): value is string =>
  typeof value === "string" && ISO_DATE.test(value);
/** Exact string only: a padded or exponent-form value is a contract violation, not a repair. */
const isExactDecimal = (value: unknown): value is string =>
  isDecimalString(value) && value === value.trim();

function oneOf<T extends string>(
  list: readonly T[],
  value: unknown,
): value is T {
  return (
    typeof value === "string" && (list as readonly string[]).includes(value)
  );
}

function fail(reason: string): never {
  throw new FilingsContractError(reason);
}

function parseIndexItem(value: unknown, at: string): FilingIndexItem {
  if (!isObj(value)) return fail(`${at} is not an object.`);
  if (typeof value.accession !== "string" || !ACCESSION.test(value.accession)) {
    return fail(`${at}.accession is not a dashed accession.`);
  }
  if (
    value.company_cik !== null &&
    !(typeof value.company_cik === "string" && CIK.test(value.company_cik))
  ) {
    return fail(`${at}.company_cik is not a ten-digit CIK or null.`);
  }
  if (!oneOf(FILING_FORMS, value.form))
    return fail(`${at}.form is not a known form.`);
  if (!isDate(value.filing_date))
    return fail(`${at}.filing_date is not a date.`);
  if (value.report_date !== null && !isDate(value.report_date)) {
    return fail(`${at}.report_date is not a date or null.`);
  }
  if (value.fsds_period_end !== null && !isDate(value.fsds_period_end)) {
    return fail(`${at}.fsds_period_end is not a date or null.`);
  }
  return {
    accession: value.accession,
    company_cik: value.company_cik,
    form: value.form,
    filing_date: value.filing_date,
    report_date: value.report_date,
    fsds_period_end: value.fsds_period_end,
  };
}

export function parseFilingIndexPage(value: unknown): FilingIndexPage {
  if (
    !isObj(value) ||
    !Array.isArray(value.items) ||
    !isNonNegativeInt(value.total) ||
    !isNonNegativeInt(value.limit) ||
    !isNonNegativeInt(value.offset)
  ) {
    return fail("expected { items, total, limit, offset }.");
  }
  return {
    items: value.items.map((item, index) =>
      parseIndexItem(item, `items[${index}]`),
    ),
    total: value.total,
    limit: value.limit,
    offset: value.offset,
  };
}

function parseFact(value: unknown, at: string): FactEvidence | null {
  if (value === null) return null;
  if (!isObj(value)) return fail(`${at} is not an object or null.`);
  if (
    !isText(value.fact_id) ||
    !isText(value.tag) ||
    !isText(value.taxonomy_version) ||
    !isText(value.unit) ||
    !isDate(value.period_end) ||
    !isNonNegativeInt(value.qtrs) ||
    typeof value.segments !== "string" ||
    typeof value.coreg !== "string"
  ) {
    return fail(`${at} does not match the documented fact shape.`);
  }
  if (value.value !== null && !isExactDecimal(value.value)) {
    return fail(`${at}.value is not an exact decimal string or null.`);
  }
  return {
    fact_id: value.fact_id,
    tag: value.tag,
    taxonomy_version: value.taxonomy_version,
    unit: value.unit,
    period_end: value.period_end,
    qtrs: value.qtrs,
    value: value.value,
    segments: value.segments,
    coreg: value.coreg,
  };
}

/**
 * The documented fact presence: only AVAILABLE (valued) and the null-value status (value null)
 * carry a fact; every refusal carries none. A fact under any other status is a contract violation,
 * so a refused value can never be shown as if it were usable.
 */
function requireFactShape(
  section: string,
  status: string,
  nullStatus: string,
  fact: FactEvidence | null,
): void {
  if (status === "AVAILABLE") {
    if (fact === null || fact.value === null)
      fail(`${section} is AVAILABLE without a valued fact.`);
  } else if (status === nullStatus) {
    if (fact === null || fact.value !== null) {
      fail(`${section} is ${nullStatus} without a fact whose value is null.`);
    }
  } else if (fact !== null) {
    fail(`${section} is ${status} but carries a fact.`);
  }
}

function parseCurrent(value: unknown): CurrentEps {
  if (!isObj(value)) return fail("current is not an object.");
  if (!oneOf(EPS_STATUSES, value.status))
    return fail("current.status is not a known status.");
  if (!isText(value.reason)) return fail("current.reason is missing.");
  const fact = parseFact(value.fact, "current.fact");
  requireFactShape("current", value.status, "NULL_VALUE", fact);
  return { status: value.status, reason: value.reason, fact };
}

function parseComparative(value: unknown): ComparativeEps {
  if (!isObj(value)) return fail("comparative is not an object.");
  if (!oneOf(COMPARATIVE_STATUSES, value.status)) {
    return fail("comparative.status is not a known status.");
  }
  if (!isText(value.reason)) return fail("comparative.reason is missing.");
  if (!isText(value.meaning)) return fail("comparative.meaning is missing.");
  const fact = parseFact(value.fact, "comparative.fact");
  requireFactShape("comparative", value.status, "COMPARATIVE_NULL_VALUE", fact);
  return {
    status: value.status,
    reason: value.reason,
    meaning: value.meaning,
    fact,
  };
}

function parseChange(value: unknown): EpsChange {
  if (!isObj(value)) return fail("change is not an object.");
  if (!oneOf(CHANGE_STATUSES, value.status))
    return fail("change.status is not a known status.");
  if (value.percentage_semantics !== "ratio") {
    return fail("change.percentage_semantics is not 'ratio'.");
  }
  if (value.eps_change !== null && !isExactDecimal(value.eps_change)) {
    return fail("change.eps_change is not an exact decimal string or null.");
  }
  if (value.transition !== null && !oneOf(TRANSITIONS, value.transition)) {
    return fail("change.transition is not a known transition.");
  }
  if (
    value.percentage_change !== null &&
    !isExactDecimal(value.percentage_change)
  ) {
    return fail(
      "change.percentage_change is not an exact decimal string or null.",
    );
  }
  if (
    value.percentage_unavailable_reason !== null &&
    !oneOf(PERCENTAGE_REASONS, value.percentage_unavailable_reason)
  ) {
    return fail("change.percentage_unavailable_reason is not a known reason.");
  }
  if (
    value.status === "AVAILABLE" &&
    (value.eps_change === null || value.transition === null)
  ) {
    return fail("change is AVAILABLE without eps_change and transition.");
  }
  if (
    value.status === "INPUT_UNAVAILABLE" &&
    (value.eps_change !== null || value.transition !== null)
  ) {
    return fail("change is INPUT_UNAVAILABLE but carries a value.");
  }
  if (
    value.percentage_change !== null &&
    value.percentage_unavailable_reason !== null
  ) {
    return fail(
      "change states both a percentage and a reason it is unavailable.",
    );
  }
  return {
    status: value.status,
    eps_change: value.eps_change,
    transition: value.transition,
    percentage_change: value.percentage_change,
    percentage_unavailable_reason: value.percentage_unavailable_reason,
  };
}

export function parseFilingEps(value: unknown): FilingEps {
  if (!isObj(value)) return fail("expected an object.");
  if (typeof value.accession !== "string" || !ACCESSION.test(value.accession)) {
    return fail("accession is not a dashed accession.");
  }
  if (
    value.company_cik !== null &&
    !(typeof value.company_cik === "string" && CIK.test(value.company_cik))
  ) {
    return fail("company_cik is not a ten-digit CIK or null.");
  }
  if (value.form !== null && !oneOf(FILING_FORMS, value.form))
    return fail("form is not a known form.");
  if (!oneOf(PERIOD_CLASSES, value.period_class))
    return fail("period_class is not known.");
  if (!oneOf(EPS_KINDS, value.eps_kind)) return fail("eps_kind is not known.");
  if (
    value.descriptive_only !== true ||
    value.no_point_in_time_claim !== true
  ) {
    return fail(
      "the descriptive-only / no-point-in-time flags are not both true.",
    );
  }
  return {
    accession: value.accession,
    company_cik: value.company_cik,
    form: value.form,
    period_class: value.period_class,
    eps_kind: value.eps_kind,
    current: parseCurrent(value.current),
    comparative: parseComparative(value.comparative),
    change: parseChange(value.change),
  };
}

export const ACCESSION_PATTERN = ACCESSION;
export const CIK_PATTERN = CIK;
