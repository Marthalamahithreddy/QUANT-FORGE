import {
  arrayOf,
  boolean,
  decimalString,
  integer,
  isoDate,
  isoInstant,
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
  AVAILABILITY_BASES,
  EARNINGS_TIMINGS,
  EPS_SURPRISE_STATUSES,
  PAGE_LIMIT_MAX,
  PAGE_OFFSET_MAX,
  PIT_ELIGIBILITIES,
  PROVIDER_TYPES,
  SECURITY_TYPES,
  type Availability,
  type CompanyRef,
  type EarningsEvent,
  type EarningsPage,
  type EpsSurprise,
  type ListingRef,
  type SourceRef,
} from "@/lib/api/schema";

const company = object<CompanyRef>({
  id: uuid,
  display_name: nonEmptyString,
  legal_name: string,
  cik: nullable(nonEmptyString),
});

const source = object<SourceRef>({
  name: nonEmptyString,
  provider_type: oneOf(PROVIDER_TYPES, "provider type"),
});

const listing = object<ListingRef>({
  ticker: nonEmptyString,
  mic: nonEmptyString,
  exchange_name: string,
  security_name: string,
  security_type: oneOf(SECURITY_TYPES, "security type"),
  valid_from: isoDate,
  valid_to: nullable(isoDate),
  is_primary: boolean,
  valid_on_event_date: boolean,
});

const availability = object<Availability>({
  observed_at: nullable(isoInstant),
  observed_at_basis: nullable(oneOf(AVAILABILITY_BASES, "availability basis")),
  pit_eligibility: oneOf(PIT_ELIGIBILITIES, "point-in-time eligibility"),
  pit_reason: nonEmptyString,
});

const surprise = object<EpsSurprise>({
  status: oneOf(EPS_SURPRISE_STATUSES, "EPS surprise status"),
  absolute: nullable(decimalString),
  ratio: nullable(decimalString),
  reason: nonEmptyString,
});

/** One stored earnings observation: every financial figure stays an exact string. */
export const decodeEarningsEvent: Decoder<EarningsEvent> = object<EarningsEvent>({
  id: uuid,
  company,
  source,
  listings: arrayOf(listing),
  resolved_tickers: arrayOf(nonEmptyString),
  fiscal_year: integer(),
  fiscal_quarter: nullable(integer()),
  event_date: isoDate,
  event_timestamp: nullable(isoInstant),
  timing: oneOf(EARNINGS_TIMINGS, "earnings timing"),
  currency: nullable(nonEmptyString),
  reported_eps: nullable(decimalString),
  estimated_eps: nullable(decimalString),
  reported_revenue: nullable(decimalString),
  estimated_revenue: nullable(decimalString),
  eps_surprise: surprise,
  availability,
  ingested_at: isoInstant,
});

/** A page: the limit/offset it echoes must be in the API's range and the items must fit the limit. */
export const decodeEarningsPage: Decoder<EarningsPage> = refine(
  object<EarningsPage>({
    items: arrayOf(decodeEarningsEvent),
    total: nonNegativeInteger,
    limit: integer(1, PAGE_LIMIT_MAX),
    offset: integer(0, PAGE_OFFSET_MAX),
  }),
  (page, path) => {
    if (page.items.length > page.limit) reject(`${path}.items`, "no more items than the page limit");
  },
);
