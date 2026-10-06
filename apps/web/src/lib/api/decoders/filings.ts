import {
  arrayOf,
  decimalString,
  integer,
  isoDate,
  literal,
  matching,
  nonEmptyString,
  nonNegativeInteger,
  nullable,
  object,
  oneOf,
  refine,
  reject,
  string,
  uuid,
  type Decoder,
} from "@/lib/api/decode";
import {
  ACCESSION_PATTERN,
  CHANGE_STATUSES,
  CIK_PATTERN,
  COMPARATIVE_STATUSES,
  EPS_KINDS,
  EPS_STATUSES,
  FILING_FORMS,
  PAGE_LIMIT_MAX,
  PAGE_OFFSET_MAX,
  PERCENTAGE_REASONS,
  PERIOD_CLASSES,
  TRANSITIONS,
  type ComparativeEps,
  type CurrentEps,
  type EpsChange,
  type FactEvidence,
  type FilingEps,
  type FilingIndexItem,
  type FilingIndexPage,
} from "@/lib/api/schema";

const accession = matching(ACCESSION_PATTERN, "a dashed accession (0000000000-00-000000)");
const cik = matching(CIK_PATTERN, "a ten-digit CIK");
const form = oneOf(FILING_FORMS, "filing form");

const indexItem = object<FilingIndexItem>({
  accession,
  company_cik: nullable(cik),
  form,
  filing_date: isoDate,
  report_date: nullable(isoDate),
  fsds_period_end: nullable(isoDate),
});

/** `GET /api/v1/filings`. */
export const decodeFilingIndexPage: Decoder<FilingIndexPage> = refine(
  object<FilingIndexPage>({
    items: arrayOf(indexItem),
    total: nonNegativeInteger,
    limit: integer(1, PAGE_LIMIT_MAX),
    offset: integer(0, PAGE_OFFSET_MAX),
  }),
  (page, path) => {
    if (page.items.length > page.limit) reject(`${path}.items`, "no more items than the page limit");
  },
);

const fact = object<FactEvidence>({
  fact_id: uuid,
  tag: nonEmptyString,
  taxonomy_version: nonEmptyString,
  unit: nonEmptyString,
  period_end: isoDate,
  qtrs: nonNegativeInteger,
  // Exact string, or null when the source stated none (never zero).
  value: nullable(decimalString),
  segments: string,
  coreg: string,
});

/**
 * The documented fact presence: only AVAILABLE (valued) and the null-value status (value null)
 * carry a fact; every refusal carries none. A fact under any other status is a contract violation,
 * so a refused value can never be shown as if it were usable.
 */
function requireFactShape(
  path: string,
  status: string,
  nullStatus: string,
  factValue: FactEvidence | null,
): void {
  if (status === "AVAILABLE") {
    if (factValue === null || factValue.value === null) reject(`${path}.fact`, "a valued fact for an AVAILABLE status");
  } else if (status === nullStatus) {
    if (factValue === null || factValue.value !== null) reject(`${path}.fact`, `a fact whose value is null for ${nullStatus}`);
  } else if (factValue !== null) {
    reject(`${path}.fact`, `no fact for a ${status} status`);
  }
}

const current: Decoder<CurrentEps> = refine(
  object<CurrentEps>({
    status: oneOf(EPS_STATUSES, "EPS status"),
    reason: nonEmptyString,
    fact: nullable(fact),
  }),
  (value, path) => requireFactShape(path, value.status, "NULL_VALUE", value.fact),
);

const comparative: Decoder<ComparativeEps> = refine(
  object<ComparativeEps>({
    status: oneOf(COMPARATIVE_STATUSES, "comparative EPS status"),
    reason: nonEmptyString,
    meaning: literal("comparative EPS as presented in this filing"),
    fact: nullable(fact),
  }),
  (value, path) => requireFactShape(path, value.status, "COMPARATIVE_NULL_VALUE", value.fact),
);

const change: Decoder<EpsChange> = refine(
  object<EpsChange>({
    status: oneOf(CHANGE_STATUSES, "EPS change status"),
    eps_change: nullable(decimalString),
    transition: nullable(oneOf(TRANSITIONS, "EPS transition")),
    // A RATIO string (0.5 = +50%), never a percentage.
    percentage_change: nullable(decimalString),
    percentage_semantics: literal("ratio"),
    percentage_unavailable_reason: nullable(oneOf(PERCENTAGE_REASONS, "percentage reason")),
  }),
  (value, path) => {
    if (value.status === "AVAILABLE" && (value.eps_change === null || value.transition === null)) {
      reject(path, "an AVAILABLE change to carry eps_change and transition");
    }
    if (value.status === "INPUT_UNAVAILABLE" && (value.eps_change !== null || value.transition !== null)) {
      reject(path, "an INPUT_UNAVAILABLE change to carry no value");
    }
    if (value.percentage_change !== null && value.percentage_unavailable_reason !== null) {
      reject(path, "either a percentage or the reason it is unavailable, not both");
    }
  },
);

/** `GET /api/v1/filings/{accession}/eps`: every status, including refusals, arrives as HTTP 200. */
export const decodeFilingEps: Decoder<FilingEps> = object<FilingEps>({
  accession,
  company_cik: nullable(cik),
  form: nullable(form),
  period_class: oneOf(PERIOD_CLASSES, "period class"),
  eps_kind: oneOf(EPS_KINDS, "EPS kind"),
  descriptive_only: literal(true),
  no_point_in_time_claim: literal(true),
  boundary_note: nonEmptyString,
  current,
  comparative,
  change,
});

