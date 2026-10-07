import { Suspense } from "react";

import { MarketReactionPreview } from "@/components/research/market-reaction-preview";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { FinancialValue } from "@/components/financial/financial-value";
import { EpsSurpriseValue } from "@/components/financial/eps-surprise";
import { DomainState } from "@/components/state/domain-state";
import { PitBadge } from "@/components/state/pit-badge";
import { StatusBadge } from "@/components/state/status-badge";
import { LinkButton } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { Frame } from "@/components/ui/frame";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import { PageHeader } from "@/components/ui/page-header";
import { CIK_PATTERN, type EarningsEvent, type ListingRef } from "@/lib/api/schema";
import { BASIS_LABEL, BASIS_MEANING, TIMING_LABEL } from "@/lib/availability";
import { earningsPageHref, fiscalPeriod, tickersAtEvent } from "@/lib/earnings-view";
import { filingsPageHref } from "@/lib/filings-view";
import { formatDate, formatUtcInstant } from "@/lib/format";
import { researchHref } from "@/lib/research-view";
import { TIMING_STATE } from "@/lib/state-map";

const LISTING_COLUMNS: readonly DataTableColumn<ListingRef>[] = [
  { id: "ticker", header: "Ticker", cell: (listing) => <span className="type-id">{listing.ticker}</span> },
  {
    id: "exchange",
    header: "Exchange",
    cell: (listing) => (
      <>
        {listing.exchange_name} <span className="type-id text-text-3">{listing.mic}</span>
      </>
    ),
  },
  { id: "from", header: "Valid from", cell: (listing) => <span className="num">{formatDate(listing.valid_from)}</span> },
  {
    id: "to",
    header: "Valid to",
    cell: (listing) => <span className="num">{listing.valid_to === null ? "present" : formatDate(listing.valid_to)}</span>,
  },
  { id: "primary", header: "Primary", cell: (listing) => (listing.is_primary ? "Primary" : "—") },
  {
    id: "on_date",
    header: "On the event date",
    cell: (listing) => (listing.valid_on_event_date ? "Valid" : "Not valid"),
  },
];

/** A ticker is linkable only when it satisfies the API's own filter rule (1 to 32, no edge space). */
const linkableTicker = (ticker: string): boolean => ticker.length <= 32 && ticker === ticker.trim() && ticker !== "";

/**
 * The event record (spec section 8.3.2): the stored observation, then, in its own frame, the
 * availability evidence. The descriptive observation and the point-in-time verdict never share a
 * frame: the verdict says nothing about whether the observation is valid.
 */
export function EventDetail({ event }: { readonly event: EarningsEvent }) {
  const { availability, company } = event;
  const tickers = tickersAtEvent(event).filter(linkableTicker);
  const hasCik = company.cik !== null && CIK_PATTERN.test(company.cik);

  return (
    <>
      <PageHeader
        eyebrow={`Earnings event · ${fiscalPeriod(event)}`}
        title={company.display_name}
        description={`${event.event_date} · ${event.timing === "UNKNOWN" ? "timing not stated by the source" : TIMING_LABEL[event.timing]}`}
        actions={
          <>
            <LinkButton
              href={researchHref(event.id)}
              variant="primary"
              label={`Open Research for ${company.display_name}, ${event.event_date}`}
            >
              Open Research
            </LinkButton>
            {tickers.map((ticker) => (
              <LinkButton key={ticker} href={earningsPageHref({ ticker }, 1)}>
                Earnings for {ticker}
              </LinkButton>
            ))}
            {hasCik && company.cik ? (
              <LinkButton href={filingsPageHref({ cik: company.cik }, 1)}>Filings for CIK {company.cik}</LinkButton>
            ) : null}
          </>
        }
      />

      <div className="grid items-start gap-4 xl:grid-cols-2">
        <Frame
          title="Stored event"
          note="What the source reported."
          actions={<StatusBadge kind="VALUE" mode="descriptive">Descriptive</StatusBadge>}
        >
          <KeyValueList>
            <KeyValue label="Company">
              {company.display_name}
              {company.legal_name !== company.display_name ? (
                <span className="text-text-3"> · {company.legal_name}</span>
              ) : null}
            </KeyValue>
            <KeyValue label="CIK">
              {company.cik === null ? <DomainState kind="NOT_STATED" compact /> : <span className="type-id">{company.cik}</span>}
            </KeyValue>
            <KeyValue label="Tickers on the event date">
              {event.resolved_tickers.length === 0 ? (
                <span className="text-text-3">Not resolved: no ticker is proven for this company on the event date.</span>
              ) : (
                <span className="type-id">{event.resolved_tickers.join(", ")}</span>
              )}
            </KeyValue>
            <KeyValue label="Event date">{formatDate(event.event_date)}</KeyValue>
            <KeyValue label="Event instant">
              {event.event_timestamp === null ? (
                <DomainState kind="NOT_STATED" reason="The source gave a date only." />
              ) : (
                formatUtcInstant(event.event_timestamp)
              )}
            </KeyValue>
            <KeyValue label="Timing">
              {TIMING_STATE[event.timing] === "VALUE" ? (
                <>
                  {TIMING_LABEL[event.timing]}
                  <span className="text-text-3"> · as stated by the source</span>
                </>
              ) : (
                <DomainState kind="NOT_STATED" reason="The source did not state when in the day the report was made." />
              )}
            </KeyValue>
            <KeyValue label="Reported EPS">
              <FinancialValue kind="eps" value={event.reported_eps} />
            </KeyValue>
            <KeyValue label="Estimated EPS">
              <FinancialValue kind="eps" value={event.estimated_eps} />
            </KeyValue>
            <KeyValue label="EPS surprise">
              <EpsSurpriseValue
                status={event.eps_surprise.status}
                absolute={event.eps_surprise.absolute}
                ratio={event.eps_surprise.ratio}
                reason={event.eps_surprise.reason}
              />
            </KeyValue>
            <KeyValue label="Currency">
              {event.currency === null ? <DomainState kind="NOT_STATED" compact /> : event.currency}
            </KeyValue>
            <KeyValue label="Reported revenue">
              <FinancialValue kind="money" value={event.reported_revenue} />
            </KeyValue>
            <KeyValue label="Estimated revenue">
              <FinancialValue kind="money" value={event.estimated_revenue} />
            </KeyValue>
          </KeyValueList>
        </Frame>

        <Frame
          title="Availability"
          note="When this could have been known."
          actions={<PitBadge eligibility={availability.pit_eligibility} />}
        >
          <KeyValueList>
            <KeyValue label="Observed at">
              {availability.observed_at === null ? (
                <DomainState kind="NOT_STATED" reason="The source did not supply a historical publication time." />
              ) : (
                formatUtcInstant(availability.observed_at)
              )}
            </KeyValue>
            <KeyValue label="Evidence basis">
              {availability.observed_at_basis === null ? (
                <DomainState kind="NOT_STATED" compact />
              ) : (
                <>
                  {BASIS_LABEL[availability.observed_at_basis]}
                  <div className="type-meta">{BASIS_MEANING[availability.observed_at_basis]}</div>
                </>
              )}
            </KeyValue>
            <KeyValue label="Point-in-time verdict">
              <div className="text-text-2">{availability.pit_reason}</div>
              {availability.pit_eligibility === "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS" ? (
                <div className="type-meta">This basis is not currently approved for point-in-time use.</div>
              ) : null}
            </KeyValue>
          </KeyValueList>
          <p className="type-meta mt-3">
            The verdict is about what could have been known and acted on. It does not change the stored event beside it.
          </p>
        </Frame>
      </div>

      <Suspense fallback={<SkeletonRegion label="Loading the market-reaction preview"><Skeleton height="9rem" /></SkeletonRegion>}>
        <MarketReactionPreview eventId={event.id} />
      </Suspense>

      <DataTable
        caption="Listings of this company"
        columns={LISTING_COLUMNS}
        rows={event.listings}
        rowKey={(listing) => `${listing.mic}-${listing.ticker}-${listing.valid_from}`}
        empty={<EmptyState title="No listings are stored for this company" />}
      />

      <Frame title="Provenance">
        <KeyValueList>
          <KeyValue label="Source">{event.source.name}</KeyValue>
          <KeyValue label="Provider type">
            <span className="type-id">{event.source.provider_type}</span>
          </KeyValue>
          <KeyValue label="Stored by this system at">{formatUtcInstant(event.ingested_at)}</KeyValue>
        </KeyValueList>
      </Frame>
    </>
  );
}
