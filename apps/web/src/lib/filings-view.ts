/**
 * Display wording and URL helpers for the filings workspace.
 *
 * Wording and navigation only. Every status, transition and reason arrives from the backend; the
 * labels here neither reclassify nor interpret them. The labels are deliberately neutral: no
 * good/bad, bullish/bearish or signal language, and no colour carries meaning on its own.
 */

import type { Tone } from "@/lib/availability";
import {
  ACCESSION_PATTERN,
  CIK_PATTERN,
  EPS_KINDS,
  PERIOD_CLASSES,
  type ChangeStatus,
  type ComparativeStatus,
  type EpsKind,
  type EpsPeriodClass,
  type EpsStatus,
  type FilingForm,
  type FilingIndexItem,
  type PercentageReason,
  type Transition,
  FILING_FORMS,
} from "@/types/filings";

export const BOUNDARY_LINES = [
  "Descriptive accounting data from SEC FSDS.",
  "No point-in-time availability claim.",
  "Not a trading signal.",
] as const;

export const PERIOD_CLASS_LABEL: Record<EpsPeriodClass, string> = {
  QUARTER: "Quarter",
  ANNUAL: "Annual",
};

export const EPS_KIND_LABEL: Record<EpsKind, string> = {
  DILUTED: "Diluted",
  BASIC_AND_DILUTED: "Basic and diluted",
};

export const EPS_STATUS_LABEL: Record<EpsStatus, string> = {
  AVAILABLE: "Available",
  NOT_INGESTED: "Not ingested",
  UNSUPPORTED_FORM_PERIOD: "Form does not support this period",
  MISSING: "Missing",
  EXTENSION_ONLY: "Extension only",
  WRONG_UNIT: "Wrong unit",
  DIMENSIONAL_ONLY: "Dimensional only",
  COREG_ONLY: "Co-registrant only",
  NULL_VALUE: "Value is null",
  AMBIGUOUS: "Ambiguous",
};

export const COMPARATIVE_STATUS_LABEL: Record<ComparativeStatus, string> = {
  AVAILABLE: "Available",
  CURRENT_UNAVAILABLE: "Current EPS unavailable",
  COMPARATIVE_MISSING: "Comparative missing",
  COMPARATIVE_PERIOD_MISMATCH: "Comparative period mismatch",
  COMPARATIVE_DIMENSIONAL_ONLY: "Comparative dimensional only",
  COMPARATIVE_COREG_ONLY: "Comparative co-registrant only",
  COMPARATIVE_WRONG_UNIT: "Comparative wrong unit",
  COMPARATIVE_NULL_VALUE: "Comparative value is null",
  COMPARATIVE_AMBIGUOUS: "Comparative ambiguous",
};

export const CHANGE_STATUS_LABEL: Record<ChangeStatus, string> = {
  AVAILABLE: "Available",
  INPUT_UNAVAILABLE: "Input unavailable",
};

export const TRANSITION_LABEL: Record<Transition, string> = {
  POSITIVE_INCREASED: "EPS increased",
  POSITIVE_DECREASED: "EPS decreased",
  POSITIVE_UNCHANGED: "EPS unchanged",
  LOSS_NARROWED: "Loss per share narrowed",
  LOSS_WIDENED: "Loss per share widened",
  LOSS_UNCHANGED: "Loss per share unchanged",
  LOSS_TO_PROFIT: "Loss to profit",
  PROFIT_TO_LOSS: "Profit to loss",
  ZERO_TO_PROFIT: "Zero EPS to profit",
  ZERO_TO_LOSS: "Zero EPS to loss",
  PROFIT_TO_ZERO: "Profit to zero EPS",
  LOSS_TO_ZERO: "Loss to zero EPS",
  ZERO_UNCHANGED: "EPS remained zero",
};

export const PERCENTAGE_REASON_DETAIL: Record<PercentageReason, string> = {
  ZERO_COMPARATIVE: "The comparative EPS is zero.",
  NEGATIVE_COMPARATIVE: "The comparative EPS is negative.",
};

/** Caution marks a refusal; it is never a verdict on the figures. */
export function statusTone(status: string): Tone {
  return status === "AVAILABLE" ? "neutral" : "caution";
}

export function isFilingForm(value: string): value is FilingForm {
  return (FILING_FORMS as readonly string[]).includes(value);
}

export function isPeriodClass(value: string): value is EpsPeriodClass {
  return (PERIOD_CLASSES as readonly string[]).includes(value);
}

export function isEpsKind(value: string): value is EpsKind {
  return (EPS_KINDS as readonly string[]).includes(value);
}

export const isAccession = (value: string): boolean =>
  ACCESSION_PATTERN.test(value);
export const isCik = (value: string): boolean => CIK_PATTERN.test(value);
/** `isCik` as a selector guard, for the optional `cik` URL parameter. */
export function isCikValue(value: string): value is string {
  return CIK_PATTERN.test(value);
}

/**
 * The period class a LIST LINK pre-selects for a form. A navigation convenience only: the detail
 * page shows it as an ordinary, changeable selection, and the API always receives it explicitly.
 */
export function navigationPeriodClass(form: FilingForm): EpsPeriodClass | null {
  if (form === "10-Q" || form === "10-Q/A") return "QUARTER";
  if (form === "10-K" || form === "10-K/A") return "ANNUAL";
  return null;
}

/** The EPS kind a list link starts with: a visible initial choice, never a fallback. */
export const INITIAL_EPS_KIND: EpsKind = "DILUTED";

/**
 * `/filings/{accession}` with explicit, correctly encoded selectors. A row whose company has a CIK
 * carries it, so a joint accession leads to the registrant the row names; a row without one stays
 * accession-only (the API then resolves the accession alone, which can be ambiguous). No identity
 * is invented for a CIK-less row.
 */
export function filingDetailHref(
  item: Pick<FilingIndexItem, "accession" | "form" | "company_cik">,
): string {
  const params = new URLSearchParams();
  if (item.company_cik !== null) params.set("cik", item.company_cik);
  const period = navigationPeriodClass(item.form);
  if (period !== null) params.set("period_class", period);
  params.set("eps_kind", INITIAL_EPS_KIND);
  return `/filings/${encodeURIComponent(item.accession)}?${params.toString()}`;
}

export type Selector<T> =
  | { readonly state: "missing" }
  | { readonly state: "invalid"; readonly raw: string }
  | { readonly state: "ok"; readonly value: T };

/**
 * Reads one URL selector. Absent or empty is `missing`; anything else that is not exactly a
 * supported wire value (wrong case, repeated parameter) is `invalid`. Nothing is coerced.
 */
export function readSelector<T extends string>(
  raw: string | readonly string[] | undefined,
  accepts: (value: string) => value is T,
): Selector<T> {
  if (raw === undefined || raw === "") return { state: "missing" };
  if (typeof raw !== "string") return { state: "invalid", raw: raw.join(", ") };
  return accepts(raw) ? { state: "ok", value: raw } : { state: "invalid", raw };
}
