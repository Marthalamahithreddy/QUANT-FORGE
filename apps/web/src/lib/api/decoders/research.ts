import {
  arrayOf,
  boolean,
  decimalString,
  integer,
  isoDate,
  isoInstant,
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
import { decodeFilingEps } from "@/lib/api/decoders/filings";
import {
  ABNORMAL_RETURN_STATUSES,
  ACCESSION_PATTERN,
  ACCOUNTING_STATES,
  ALIGNMENT_STATUSES,
  ARTIFACT_ROLES,
  AVAILABILITY_BASES,
  BENCHMARK_RETURN_STATUSES,
  CIK_PATTERN,
  DISCLOSURE_LEVELS,
  EARNINGS_TIMINGS,
  EPS_KINDS,
  EPS_SURPRISE_STATUSES,
  FILING_FORMS,
  FILING_LINK_STATUSES,
  FORWARD_RETURN_STATUSES,
  HORIZON_MAX_COUNT,
  HORIZON_MAX_VALUE,
  LISTING_EVIDENCE,
  LISTING_RESOLUTION_STATUSES,
  PAGE_LIMIT_MAX,
  PAGE_OFFSET_MAX,
  PERIOD_CLASSES,
  PERIOD_VERIFICATIONS,
  PIT_ELIGIBILITIES,
  POINT_IN_TIME_STATES,
  PRICE_BASES,
  REFUSAL_SCOPES,
  RESEARCH_MODES,
  SECTION_STATES,
  type AbnormalReturn,
  type AccountingSection,
  type Alignment,
  type ArtifactDigest,
  type BenchmarkItem,
  type BenchmarkPage,
  type BenchmarkReturn,
  type BenchmarkSection,
  type DescriptiveSection,
  type DisclosureEvidence,
  type FilingLink,
  type ForwardReturn,
  type ListingResolution,
  type Observation,
  type ObservationEpsSurprise,
  type ObservationEvent,
  type ObservationIdentity,
  type ObservationParameters,
  type ObservationProvenance,
  type PointInTimeSection,
  type Refusal,
  type SessionAlignment,
  type StockReturns,
  type Study,
} from "@/lib/api/schema";
import { REFUSAL_CODE_STATE } from "@/lib/state-map";

const exact = nullable(decimalString);
const date = nullable(isoDate);
const maybeUuid = nullable(uuid);
const accession = matching(ACCESSION_PATTERN, "a dashed accession (0000000000-00-000000)");
const cik = matching(CIK_PATTERN, "a ten-digit CIK");
const form = oneOf(FILING_FORMS, "filing form");

// ── benchmarks ───────────────────────────────────────────────────────────────────────────

const benchmarkItem = object<BenchmarkItem>({
  id: uuid,
  name: nonEmptyString,
  symbol: nullable(nonEmptyString),
  currency: nullable(nonEmptyString),
  is_active: boolean,
});

/** `GET /api/v1/benchmarks`: identity fields only, active and inactive alike. */
export const decodeBenchmarkPage: Decoder<BenchmarkPage> = refine(
  object<BenchmarkPage>({
    items: arrayOf(benchmarkItem),
    total: nonNegativeInteger,
    limit: integer(1, PAGE_LIMIT_MAX),
    offset: integer(0, PAGE_OFFSET_MAX),
  }),
  (page, path) => {
    if (page.items.length > page.limit) reject(`${path}.items`, "no more items than the page limit");
  },
);

// ── observation parts ────────────────────────────────────────────────────────────────────

const listingResolution = object<ListingResolution>({
  status: oneOf(LISTING_RESOLUTION_STATUSES, "listing resolution status"),
  listing_id: maybeUuid,
  security_id: maybeUuid,
  exchange_mic: nullable(nonEmptyString),
  evidence: nullable(oneOf(LISTING_EVIDENCE, "listing evidence")),
  reason: nonEmptyString,
});

const identity = object<ObservationIdentity>({
  company_id: uuid,
  company_cik: nullable(cik),
  event_id: uuid,
  listing_resolution: listingResolution,
  benchmark_id: maybeUuid,
  filing_accession: nullable(accession),
});

const horizonList = refine(arrayOf(integer(1, HORIZON_MAX_VALUE)), (horizons, path) => {
  if (horizons.length < 1 || horizons.length > HORIZON_MAX_COUNT) reject(path, `from 1 to ${HORIZON_MAX_COUNT} horizons`);
  for (let index = 1; index < horizons.length; index += 1) {
    if ((horizons[index] ?? 0) <= (horizons[index - 1] ?? 0)) reject(path, "horizons ascending and without duplicates");
  }
});

const parameters = object<ObservationParameters>({
  horizons: horizonList,
  price_basis: oneOf(PRICE_BASES, "price basis"),
  security_id: maybeUuid,
  benchmark_id: maybeUuid,
  filing_accession: nullable(accession),
  eps_period_class: nullable(oneOf(PERIOD_CLASSES, "period class")),
  eps_kind: nullable(oneOf(EPS_KINDS, "EPS kind")),
});

const event = object<ObservationEvent>({
  event_id: uuid,
  fingerprint: nonEmptyString,
  fiscal_year: integer(),
  fiscal_quarter: nullable(integer()),
  event_date: isoDate,
  event_timestamp: nullable(isoInstant),
  timing: oneOf(EARNINGS_TIMINGS, "earnings timing"),
  reported_eps: exact,
  estimated_eps: exact,
  observed_at: nullable(isoInstant),
  observed_at_basis: nullable(oneOf(AVAILABILITY_BASES, "availability basis")),
});

const filingLink = object<FilingLink>({
  status: oneOf(FILING_LINK_STATUSES, "filing-link status"),
  period_verification: oneOf(PERIOD_VERIFICATIONS, "period verification"),
  accession_number: nullable(accession),
  company_cik: nullable(cik),
  form: nullable(form),
  reason: nonEmptyString,
});

const disclosure = object<DisclosureEvidence>({
  level: oneOf(DISCLOSURE_LEVELS, "disclosure-evidence level"),
  accession_number: nullable(accession),
  form: nullable(form),
  items: nullable(string),
});

const accounting: Decoder<AccountingSection> = refine(
  object<AccountingSection>({
    state: oneOf(ACCOUNTING_STATES, "accounting state"),
    eps: nullable(decodeFilingEps),
  }),
  (section, path) => {
    // The EPS contract is present exactly when the accounting was evaluated.
    if ((section.state === "EVALUATED") !== (section.eps !== null)) {
      reject(`${path}.eps`, "the filing EPS to be present only when the accounting state is EVALUATED");
    }
  },
);

const forwardReturn = object<ForwardReturn>({
  horizon: integer(1, HORIZON_MAX_VALUE),
  status: oneOf(FORWARD_RETURN_STATUSES, "forward-return status"),
  anchor_session: date,
  anchor_close: exact,
  target_session: date,
  target_close: exact,
  return_value: exact,
  reason: nonEmptyString,
});

const stockReturns = object<StockReturns>({
  listing_id: maybeUuid,
  price_source_id: maybeUuid,
  anchor_session: date,
  forward_returns: arrayOf(forwardReturn),
});

const benchmarkReturn = object<BenchmarkReturn>({
  horizon: integer(1, HORIZON_MAX_VALUE),
  status: oneOf(BENCHMARK_RETURN_STATUSES, "benchmark-return status"),
  anchor_session: date,
  anchor_close: exact,
  target_session: date,
  target_close: exact,
  benchmark_return: exact,
  reason: nonEmptyString,
});

const benchmark = object<BenchmarkSection>({
  benchmark_id: maybeUuid,
  returns: arrayOf(benchmarkReturn),
});

const abnormalReturn = object<AbnormalReturn>({
  horizon: integer(1, HORIZON_MAX_VALUE),
  status: oneOf(ABNORMAL_RETURN_STATUSES, "abnormal-return status"),
  abnormal_return: exact,
  reason: nonEmptyString,
});

const sessionAlignment = object<SessionAlignment>({
  status: oneOf(ALIGNMENT_STATUSES, "alignment status"),
  event_session: date,
  first_post_event_session: date,
  reason: nonEmptyString,
});

const alignment = object<Alignment>({
  event_date: isoDate,
  timing: oneOf(EARNINGS_TIMINGS, "earnings timing"),
  session_alignment: nullable(sessionAlignment),
});

const epsSurprise = object<ObservationEpsSurprise>({
  status: oneOf(EPS_SURPRISE_STATUSES, "EPS surprise status"),
  reported_eps: exact,
  estimated_eps: exact,
  absolute_surprise: exact,
  surprise_ratio: exact,
  reason: nonEmptyString,
});

const study: Decoder<Study> = refine(
  object<Study>({
    research_mode: oneOf(RESEARCH_MODES, "research mode"),
    point_in_time_eligibility: oneOf(PIT_ELIGIBILITIES, "point-in-time eligibility"),
    eps_surprise: epsSurprise,
    alignment,
    stock_returns: stockReturns,
    benchmark,
    abnormal_returns: arrayOf(abnormalReturn),
    price_basis_note: nonEmptyString,
  }),
  (value, path) => {
    // Every horizon appears once in each per-horizon list, in the same order: the page pairs them
    // by position, so a mismatch would silently show one horizon's figure under another.
    const horizons = value.stock_returns.forward_returns.map((entry) => entry.horizon);
    const same = (other: readonly { horizon: number }[]) =>
      other.length === horizons.length && other.every((entry, index) => entry.horizon === horizons[index]);
    if (!same(value.benchmark.returns)) reject(`${path}.benchmark.returns`, "the same horizons as the stock returns");
    if (!same(value.abnormal_returns)) reject(`${path}.abnormal_returns`, "the same horizons as the stock returns");
    // A computed figure exists exactly when its status is AVAILABLE.
    for (const entry of value.stock_returns.forward_returns) {
      if ((entry.status === "AVAILABLE") !== (entry.return_value !== null)) {
        reject(`${path}.stock_returns.forward_returns`, "a return value exactly when the status is AVAILABLE");
      }
    }
    for (const entry of value.benchmark.returns) {
      if ((entry.status === "AVAILABLE") !== (entry.benchmark_return !== null)) {
        reject(`${path}.benchmark.returns`, "a benchmark return exactly when the status is AVAILABLE");
      }
    }
    for (const entry of value.abnormal_returns) {
      if ((entry.status === "AVAILABLE") !== (entry.abnormal_return !== null)) {
        reject(`${path}.abnormal_returns`, "an abnormal return exactly when the status is AVAILABLE");
      }
    }
  },
);

const descriptive = object<DescriptiveSection>({
  state: oneOf(SECTION_STATES, "section state"),
  study,
});

const pointInTime: Decoder<PointInTimeSection> = refine(
  object<PointInTimeSection>({
    state: oneOf(POINT_IN_TIME_STATES, "point-in-time state"),
    eligibility: oneOf(PIT_ELIGIBILITIES, "point-in-time eligibility"),
    study: nullable(study),
  }),
  (section, path) => {
    // The study exists exactly when the evidence is eligible: a refused section has none.
    if ((section.state === "EVIDENCE_ELIGIBLE") !== (section.study !== null)) {
      reject(`${path}.study`, "a point-in-time study exactly when the state is EVIDENCE_ELIGIBLE");
    }
  },
);

const artifact = object<ArtifactDigest>({
  role: oneOf(ARTIFACT_ROLES, "artifact role"),
  sha256: matching(/^[0-9a-f]{64}$/, "a SHA-256 hex digest"),
});

const provenance = object<ObservationProvenance>({
  event_fingerprint: nonEmptyString,
  event_source_id: uuid,
  event_ingestion_run_id: maybeUuid,
  stock_price_source_id: maybeUuid,
  benchmark_id: maybeUuid,
  filing_source_id: maybeUuid,
  filing_ingestion_run_id: maybeUuid,
  accounting_artifacts: arrayOf(artifact),
});

const refusal: Decoder<Refusal> = refine(
  object<Refusal>({
    scope: oneOf(REFUSAL_SCOPES, "refusal scope"),
    // The originating layer's own status name (free text in the OpenAPI schema).
    code: nonEmptyString,
    reason: nonEmptyString,
    horizon: nullable(integer(1, HORIZON_MAX_VALUE)),
  }),
  (entry, path) => {
    // ...but each scope draws from known vocabularies only. An unknown code is drift, not a state
    // this app may invent.
    // (A value status such as AVAILABLE or ALIGNED is never a refusal.)
    const state = Object.hasOwn(REFUSAL_CODE_STATE[entry.scope], entry.code) ? REFUSAL_CODE_STATE[entry.scope][entry.code] : undefined;
    if (state === undefined || state === "VALUE") {
      reject(`${path}.code`, `a status code known to the ${entry.scope} scope`);
    }
  },
);

/**
 * `GET /api/v1/research/observations/{event_id}`. Both research modes arrive in ONE response; a
 * domain refusal is a section of it (HTTP 200), never an error. Every figure stays an exact string.
 */
export const decodeObservation: Decoder<Observation> = object<Observation>({
  identity,
  parameters,
  event,
  filing_link: filingLink,
  disclosure_evidence: disclosure,
  accounting,
  descriptive,
  point_in_time: pointInTime,
  provenance,
  refusals: arrayOf(refusal),
});
