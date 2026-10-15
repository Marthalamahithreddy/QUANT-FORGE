import {
  arrayOf,
  decimalString,
  integer,
  isoDate,
  isoInstant,
  literal,
  matching,
  nonEmptyString,
  nonNegativeInteger,
  nullable,
  object,
  oneOf,
  refine,
  reject,
  uuid,
  type Decoder,
} from "@/lib/api/decode";
import {
  ACCESSION_PATTERN,
  CHANGE_STATUSES,
  CIK_PATTERN,
  FILING_FORMS,
  METRIC_IDS,
  PAGE_LIMIT_MAX,
  PAGE_OFFSET_MAX,
  PERCENTAGE_REASONS,
  PERIOD_CLASSES,
  POINT_STATUSES,
  TRANSITIONS,
  type CompanyDirectoryItem,
  type CompanyDirectoryPage,
  type CompanyFinancials,
  type FactSource,
  type FilingFinancials,
  type MetricSection,
  type PointSection,
} from "@/lib/api/schema";

const cik = matching(CIK_PATTERN, "a ten-digit CIK");
const accession = matching(ACCESSION_PATTERN, "a dashed accession (0000000000-00-000000)");

const directoryItem = object<CompanyDirectoryItem>({
  cik,
  name: nonEmptyString,
  tickers: arrayOf(nonEmptyString),
  stored_filings: nonNegativeInteger,
  filings_with_financial_facts: nonNegativeInteger,
});

/** `GET /api/v1/companies`. */
export const decodeCompanyDirectoryPage: Decoder<CompanyDirectoryPage> = refine(
  object<CompanyDirectoryPage>({
    items: arrayOf(directoryItem),
    total: nonNegativeInteger,
    limit: integer(1, PAGE_LIMIT_MAX),
    offset: integer(0, PAGE_OFFSET_MAX),
  }),
  (page, path) => {
    if (page.items.length > page.limit) reject(`${path}.items`, "no more items than the page limit");
  },
);

const factSource = object<FactSource>({
  fact_id: uuid,
  tag: nonEmptyString,
  taxonomy_version: nonEmptyString,
  unit: nonEmptyString,
  period_end: isoDate,
  qtrs: nonNegativeInteger,
  // Exact string, or null when the source stated none (never zero).
  value: nullable(decimalString),
});

/**
 * A fact is present exactly for AVAILABLE (valued) and NULL_VALUE (value null); any refusal that
 * carried a fact would let a refused figure be shown as if it were usable.
 */
const point: Decoder<PointSection> = refine(
  object<PointSection>({
    status: oneOf(POINT_STATUSES, "figure status"),
    reason: nonEmptyString,
    fact: nullable(factSource),
  }),
  (value, path) => {
    if (value.status === "AVAILABLE") {
      if (value.fact === null || value.fact.value === null) reject(`${path}.fact`, "a valued fact for an AVAILABLE status");
    } else if (value.status === "NULL_VALUE") {
      if (value.fact === null || value.fact.value !== null) reject(`${path}.fact`, "a fact whose value is null for NULL_VALUE");
    } else if (value.fact !== null) {
      reject(`${path}.fact`, `no fact for a ${value.status} status`);
    }
  },
);

const metric: Decoder<MetricSection> = refine(
  object<MetricSection>({
    metric: oneOf(METRIC_IDS, "metric"),
    current: point,
    prior: point,
    change: refine(
      object<MetricSection["change"]>({
        status: oneOf(CHANGE_STATUSES, "change status"),
        absolute: nullable(decimalString),
        transition: nullable(oneOf(TRANSITIONS, "transition")),
        ratio: nullable(decimalString),
        ratio_unavailable_reason: nullable(oneOf(PERCENTAGE_REASONS, "ratio reason")),
      }),
      (value, path) => {
        if (value.status === "AVAILABLE" && (value.absolute === null || value.transition === null)) {
          reject(path, "an AVAILABLE change to carry a value and a transition");
        }
        if (value.status === "INPUT_UNAVAILABLE" && (value.absolute !== null || value.ratio !== null)) {
          reject(path, "an INPUT_UNAVAILABLE change to carry no value");
        }
        if (value.ratio !== null && value.ratio_unavailable_reason !== null) {
          reject(path, "either a ratio or the reason it is unavailable, not both");
        }
      },
    ),
  }),
  (value, path) => {
    const bothAvailable = value.current.status === "AVAILABLE" && value.prior.status === "AVAILABLE";
    if (!bothAvailable && value.change.status === "AVAILABLE") {
      reject(`${path}.change`, "no change unless both figures are AVAILABLE");
    }
  },
);

const filing = object<FilingFinancials>({
  accession,
  form: oneOf(FILING_FORMS, "filing form"),
  period_class: oneOf(PERIOD_CLASSES, "period class"),
  period_end: nullable(isoDate),
  fiscal_year: nullable(integer(1900, 2100)),
  fiscal_period: nullable(nonEmptyString),
  filing_date: isoDate,
  report_date: nullable(isoDate),
  package_stored_at: isoInstant,
  source_artifact: nonEmptyString,
  source_artifact_sha256: matching(/^[0-9a-f]{64}$/, "a SHA-256 hex digest"),
  source_url: matching(
    /^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\/\d+\/\d{18}\/\d{10}-\d{2}-\d{6}-index\.htm$/,
    "an SEC EDGAR filing-index link",
  ),
  metrics: arrayOf(metric),
});

/** `GET /api/v1/companies/{cik}/financials`. */
export const decodeCompanyFinancials: Decoder<CompanyFinancials> = object<CompanyFinancials>({
  company: object<CompanyFinancials["company"]>({
    cik,
    name: nonEmptyString,
    tickers: arrayOf(nonEmptyString),
  }),
  descriptive_only: literal(true),
  no_point_in_time_claim: literal(true),
  boundary_note: nonEmptyString,
  filings: arrayOf(filing),
});
