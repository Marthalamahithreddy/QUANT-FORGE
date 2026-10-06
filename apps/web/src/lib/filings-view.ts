/**
 * Display wording and URL helpers for the filings workspace.
 *
 * Wording and navigation only. Every status, transition and reason arrives from the backend; the
 * labels here neither reclassify nor interpret them. The labels are deliberately neutral: no
 * good/bad, bullish/bearish or signal language, and no colour carries meaning on its own.
 */

import type { FilingsQuery } from "@/lib/api/filings";
import {
  ACCESSION_PATTERN,
  CIK_PATTERN,
  EPS_KINDS,
  FILING_FORMS,
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
} from "@/lib/api/schema";
import {
  canonicalHref,
  isCanonical,
  offsetOfPage,
  parsePage,
  readParam,
  type ListError,
  type RawSearchParams,
} from "@/lib/list-query";

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
/** A ten-digit CIK, as a type guard so it can drive `readSelector` for the optional `cik` parameter. */
export function isCik(value: string): value is string {
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

/** The address of the filing view for a validated selection (used for "Try again"). */
export function filingViewHref(
  accession: string,
  selection: { cik: string | null; period: EpsPeriodClass; kind: EpsKind },
): string {
  const params = new URLSearchParams();
  if (selection.cik !== null) params.set("cik", selection.cik);
  params.set("period_class", selection.period);
  params.set("eps_kind", selection.kind);
  return `/filings/${encodeURIComponent(accession)}?${params.toString()}`;
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

// ── filings list URL state ───────────────────────────────────────────────────────────────

export const FILINGS_PATH = "/filings";

/** `10-K/A`, `10-Q/A`, `8-K/A`: formatting derived from the stored form, never from facts. */
export const isAmendment = (form: FilingForm): boolean => form.endsWith("/A");

export interface FilingsListState {
  /** What the reader typed (for the form), valid or not. */
  readonly shown: { readonly form: string; readonly cik: string };
  readonly query: FilingsQuery;
  readonly page: number;
  readonly errors: readonly ListError[];
  readonly filtered: boolean;
  readonly canonical: string | null;
  readonly redirect: boolean;
}

/** The URL of one page of a filtered filings list. */
export function filingsPageHref(query: FilingsQuery, page: number): string {
  return canonicalHref(FILINGS_PATH, [["form", query.form], ["cik", query.cik]], page);
}

export function parseFilingsList(raw: RawSearchParams): FilingsListState {
  const form = readParam(raw, "form");
  const cik = readParam(raw, "cik");
  const { page, error: pageError } = parsePage(raw);
  const errors: ListError[] = [];

  if (form.repeated || (form.text !== "" && !isFilingForm(form.text))) {
    errors.push({
      param: "form",
      message: `Form must be one of ${FILING_FORMS.join(", ")} (an exact match, as a single value).`,
    });
  }
  if (cik.repeated || (cik.text !== "" && !isCik(cik.text))) {
    errors.push({
      param: "cik",
      message: "CIK must be exactly ten digits, including leading zeros, as a single value.",
    });
  }
  if (pageError) errors.push(pageError);

  const query: FilingsQuery = {
    form: !errors.some((e) => e.param === "form") && form.text !== "" && isFilingForm(form.text) ? form.text : undefined,
    cik: !errors.some((e) => e.param === "cik") && cik.text !== "" ? cik.text : undefined,
    offset: offsetOfPage(page),
  };
  const canonical = errors.length === 0 ? filingsPageHref(query, page) : null;
  return {
    shown: { form: form.text, cik: cik.text },
    query,
    page,
    errors,
    filtered: Boolean(query.form || query.cik),
    canonical,
    redirect: canonical !== null && !isCanonical(raw, canonical),
  };
}
