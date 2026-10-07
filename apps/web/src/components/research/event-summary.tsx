import { FinancialValue } from "@/components/financial/financial-value";
import { RefusalReference } from "@/components/research/refusal-ledger";
import { EventEvidence } from "@/components/research/provenance";
import { DomainState } from "@/components/state/domain-state";
import { EvidenceDrawer } from "@/components/ui/evidence-drawer";
import { Frame } from "@/components/ui/frame";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import type { EarningsEvent, ListingResolution, Observation, ObservationEvent } from "@/lib/api/schema";
import { BASIS_LABEL, BASIS_MEANING, TIMING_LABEL } from "@/lib/availability";
import { fiscalPeriod } from "@/lib/earnings-view";
import { formatDate, formatUtcInstant } from "@/lib/format";
import { LISTING_LABEL } from "@/lib/research-wording";
import { LISTING_STATE, TIMING_STATE } from "@/lib/state-map";
import type { NonValueState } from "@/lib/state";

/**
 * The facts of one stored event and how it was tied to a listing: descriptive only (the availability
 * evidence belongs to the point-in-time frame). Used by the live workspace and by a saved baseline,
 * so the two can never word the same fact differently.
 */
export function EventFacts({ event, resolution }: { readonly event: ObservationEvent; readonly resolution: ListingResolution }) {
  const listingState = LISTING_STATE[resolution.status];
  return (
    <KeyValueList>
      <KeyValue label="Fiscal period">{fiscalPeriod(event)}</KeyValue>
      <KeyValue label="Event date">{formatDate(event.event_date)}</KeyValue>
      <KeyValue label="Event instant">
        {event.event_timestamp === null ? <DomainState kind="NOT_STATED" reason="The source gave a date only." /> : formatUtcInstant(event.event_timestamp)}
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
      <KeyValue label="Listing used">
        <span className="inline-flex flex-wrap items-center gap-x-2">
          {listingState === "VALUE" ? null : <DomainState kind={listingState as NonValueState} reason={resolution.reason} />}
          <span>{LISTING_LABEL[resolution.status]}</span>
          {resolution.exchange_mic ? <span className="type-id text-text-3">{resolution.exchange_mic}</span> : null}
        </span>
        <div className="type-meta">{resolution.reason}</div>
      </KeyValue>
      <KeyValue label="Availability evidence">
        {event.observed_at === null ? (
          <DomainState kind="NOT_STATED" reason="The source did not supply a historical availability time." />
        ) : (
          <>
            {formatUtcInstant(event.observed_at)}
            {event.observed_at_basis ? (
              <div className="type-meta">
                {BASIS_LABEL[event.observed_at_basis]}. {BASIS_MEANING[event.observed_at_basis]}
              </div>
            ) : null}
          </>
        )}
      </KeyValue>
    </KeyValueList>
  );
}

/**
 * The stored observation behind the study: what the source reported and how the event was tied to
 * a listing. Descriptive facts only; the availability evidence belongs to the point-in-time frame.
 */
export function EventSummary({ observation, record }: { readonly observation: Observation; readonly record: EarningsEvent }) {
  const { event, identity } = observation;
  return (
    <Frame
      id="section-event"
      title="Event"
      note="The stored event."
      actions={<EvidenceDrawer subject="the stored event" title="Event evidence"><EventEvidence observation={observation} record={record} /></EvidenceDrawer>}
    >
      <EventFacts event={event} resolution={identity.listing_resolution} />
      <RefusalReference scopes={["EVENT"]} refusals={observation.refusals} />
    </Frame>
  );
}
