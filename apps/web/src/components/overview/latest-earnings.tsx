import { EventDateCell } from "@/components/earnings/event-date-cell";
import { BlockLink, OVERVIEW_ROWS } from "@/components/overview/overview-block";
import { ResearchLink } from "@/components/research/research-link";
import { PitBadge } from "@/components/state/pit-badge";
import { ApiFailureState } from "@/components/ui/api-failure";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { getEarningsPage } from "@/lib/api/earnings";
import type { EarningsEvent } from "@/lib/api/schema";
import { tickersAtEvent } from "@/lib/earnings-view";

const COLUMNS: readonly DataTableColumn<EarningsEvent>[] = [
  {
    id: "event_date",
    header: "Event date",
    cell: (event) => <EventDateCell event={event} />,
  },
  {
    id: "company",
    header: "Company",
    cell: (event) => {
      const tickers = tickersAtEvent(event);
      return (
        <div className="min-w-32">
          <div className="text-text">{event.company.display_name}</div>
          {tickers.length > 0 ? <div className="type-id text-text-3">{tickers.join(", ")}</div> : null}
        </div>
      );
    },
  },
  { id: "pit", header: "Point-in-time", priority: "P2", cell: (event) => <PitBadge eligibility={event.availability.pit_eligibility} /> },
  {
    id: "research",
    header: "Research",
    cell: (event) => <ResearchLink eventId={event.id} company={event.company.display_name} eventDate={event.event_date} />,
  },
];

/**
 * The newest stored earnings events: ONE list request, in the API's order (newest event date
 * first, scheduled events included). It enriches nothing and reads no event detail.
 */
export async function LatestEarnings() {
  const result = await getEarningsPage({ limit: OVERVIEW_ROWS });
  if (result.kind !== "ok") {
    return <ApiFailureState failure={result} subject="the latest earnings events" retryHref="/" />;
  }
  const { items, total } = result.data;
  if (items.length === 0) {
    return (
      <EmptyState title="No earnings events are stored">
        Events appear here after ingestion has loaded them. Nothing is shown in the meantime.
      </EmptyState>
    );
  }
  return (
    <>
      <DataTable captionHidden caption="Latest earnings events" columns={COLUMNS} rows={items} rowKey={(event) => event.id} />
      <p className="type-meta">
        Showing {items.length} of {total} stored earnings events.
      </p>
      <BlockLink href="/earnings">Open Earnings</BlockLink>
    </>
  );
}
