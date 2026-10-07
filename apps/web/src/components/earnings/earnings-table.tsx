
import { FinancialValue } from "@/components/financial/financial-value";
import { PercentValue } from "@/components/financial/percent-value";
import { DomainState } from "@/components/state/domain-state";
import { EventDateCell } from "@/components/earnings/event-date-cell";
import { ResearchLink } from "@/components/research/research-link";
import { PitBadge } from "@/components/state/pit-badge";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import type { EarningsEvent } from "@/lib/api/schema";
import { BASIS_LABEL, PIT_PRESENTATION, TIMING_LABEL } from "@/lib/availability";
import { tickersAtEvent } from "@/lib/earnings-view";
import { formatUtcInstant } from "@/lib/format";
import type { NonValueState, StateKind } from "@/lib/state";
import { SURPRISE_STATE, TIMING_STATE } from "@/lib/state-map";

function SurpriseCell({ event }: { readonly event: EarningsEvent }) {
  const { eps_surprise: surprise } = event;
  if (surprise.absolute === null) {
    return <DomainState kind={nonValue(SURPRISE_STATE[surprise.status])} reason={surprise.reason} compact />;
  }
  return (
    <div className="flex flex-col items-end gap-0.5">
      <FinancialValue kind="signed" value={surprise.absolute} />
      {surprise.ratio === null ? (
        <DomainState kind="UNAVAILABLE" reason={surprise.reason} />
      ) : (
        <span className="text-text-3" title={surprise.reason}>
          <PercentValue ratio={surprise.ratio} />
        </span>
      )}
    </div>
  );
}

/**
 * A surprise with no figure is, by definition, not a VALUE. (`AVAILABLE` without an absolute would
 * be a backend inconsistency: it is shown as not stated rather than as a fabricated number.)
 */
function nonValue(kind: StateKind): NonValueState {
  return kind === "VALUE" ? "NOT_STATED" : kind;
}

function PitCell({ event }: { readonly event: EarningsEvent }) {
  const { availability } = event;
  return (
    <div className="flex flex-col items-start gap-0.5">
      <PitBadge eligibility={availability.pit_eligibility} />
      <span className="type-meta" title={availability.pit_reason}>
        {PIT_PRESENTATION[availability.pit_eligibility].short}
      </span>
    </div>
  );
}

const COLUMNS: readonly DataTableColumn<EarningsEvent>[] = [
  {
    id: "event_date",
    header: "Event date",
    cell: (event) => <EventDateCell event={event} />,
  },
  {
    id: "company",
    header: "Company",
    cell: (event) => (
      <div className="min-w-40">
        <div className="text-text">{event.company.display_name}</div>
        {event.company.cik ? <div className="type-id text-text-3">CIK {event.company.cik}</div> : null}
      </div>
    ),
  },
  {
    id: "ticker",
    header: "Ticker",
    cell: (event) => {
      const tickers = tickersAtEvent(event);
      return tickers.length > 0 ? (
        <span className="type-id whitespace-nowrap">{tickers.join(", ")}</span>
      ) : (
        <span className="text-text-3" title="No ticker is proven for this company on the event date.">
          Not resolved
        </span>
      );
    },
  },
  {
    id: "reported_eps",
    header: "Reported EPS",
    align: "end",
    cell: (event) => <FinancialValue kind="eps" value={event.reported_eps} />,
  },
  {
    id: "estimated_eps",
    header: "Est. EPS",
    align: "end",
    cell: (event) => <FinancialValue kind="eps" value={event.estimated_eps} />,
  },
  { id: "surprise", header: "EPS surprise", align: "end", cell: (event) => <SurpriseCell event={event} /> },
  { id: "pit", header: "Point-in-time", cell: (event) => <PitCell event={event} /> },
  {
    id: "research",
    header: "Research",
    cell: (event) => (
      <ResearchLink eventId={event.id} company={event.company.display_name} eventDate={event.event_date} />
    ),
  },
  {
    id: "timing",
    header: "Timing",
    priority: "P2",
    cell: (event) =>
      TIMING_STATE[event.timing] === "VALUE" ? (
        <span className="whitespace-nowrap text-text-2">{TIMING_LABEL[event.timing]}</span>
      ) : (
        <DomainState kind="NOT_STATED" reason="The source did not state when in the day the report was made." />
      ),
  },
  {
    id: "observed_at",
    header: "Observed at",
    priority: "P3",
    cell: (event) =>
      event.availability.observed_at === null ? (
        <DomainState kind="NOT_STATED" reason="The source did not supply a historical availability time." />
      ) : (
        <span className="num whitespace-nowrap">{formatUtcInstant(event.availability.observed_at)}</span>
      ),
  },
  {
    id: "basis",
    header: "Basis",
    priority: "P3",
    cell: (event) =>
      event.availability.observed_at_basis === null ? (
        <DomainState kind="NOT_STATED" compact />
      ) : (
        <span className="whitespace-nowrap text-text-2">{BASIS_LABEL[event.availability.observed_at_basis]}</span>
      ),
  },
  {
    id: "source",
    header: "Source",
    priority: "P3",
    cell: (event) => <span className="whitespace-nowrap text-text-2">{event.source.name}</span>,
  },
];

export interface EarningsTableProps {
  readonly events: readonly EarningsEvent[];
}

/**
 * One page of the earnings list, in the API's order (newest event date first). Every value is
 * the backend's string or classification; nothing is sorted, filtered or computed here, and a
 * revision or a second source for the same fiscal period is its own row.
 */
export function EarningsTable({ events }: EarningsTableProps) {
  return <DataTable caption="Earnings events" columns={COLUMNS} rows={events} rowKey={(event) => event.id} />;
}
