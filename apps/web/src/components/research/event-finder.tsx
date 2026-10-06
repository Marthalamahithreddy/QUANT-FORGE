import Link from "next/link";

import { FinancialValue } from "@/components/financial/financial-value";
import { StatusBadge } from "@/components/state/status-badge";
import { ApiFailureState } from "@/components/ui/api-failure";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { ListEmpty } from "@/components/ui/list-empty";
import { Pagination } from "@/components/ui/pagination";
import { getEarningsPage } from "@/lib/api/earnings";
import type { EarningsEvent } from "@/lib/api/schema";
import { PIT_PRESENTATION } from "@/lib/availability";
import { earningsPageHref, tickersAtEvent, type EarningsListState } from "@/lib/earnings-view";
import { MAX_PAGE } from "@/lib/list-query";
import { researchHref } from "@/lib/research-view";
import { PIT_STATE } from "@/lib/state-map";

export const RESEARCH_PATH = "/research";

const COLUMNS: DataTableColumn<EarningsEvent>[] = [
  {
    id: "event_date",
    header: "Event date",
    cell: (event) => (
      <Link href={`/earnings/${event.id}`} className="font-medium text-accent underline-offset-2 hover:underline">
        {event.event_date}
      </Link>
    ),
  },
  { id: "company", header: "Company", cell: (event) => <span className="text-text">{event.company.display_name}</span> },
  {
    id: "ticker",
    header: "Ticker",
    cell: (event) => {
      const tickers = tickersAtEvent(event);
      return tickers.length > 0 ? <span className="type-id">{tickers.join(", ")}</span> : <span className="text-text-3">Not resolved</span>;
    },
  },
  { id: "reported", header: "Reported EPS", align: "end", cell: (event) => <FinancialValue kind="eps" value={event.reported_eps} /> },
  {
    id: "pit",
    header: "PIT status",
    cell: (event) => {
      const state = PIT_STATE[event.availability.pit_eligibility];
      return (
        <StatusBadge kind={state} mode={state === "NOT_APPLICABLE" ? undefined : "pit"}>
          {PIT_PRESENTATION[event.availability.pit_eligibility].label}
        </StatusBadge>
      );
    },
  },
  {
    id: "study",
    header: "Study",
    cell: (event) => (
      <Link
        href={researchHref(event.id)}
        aria-label={`Open study for ${event.company.display_name}, ${event.event_date}`}
        className="text-accent underline-offset-2 hover:underline"
      >
        Open study
      </Link>
    ),
  },
];

/**
 * The research entry's event finder: a compact page of stored events, each with an "Open study"
 * link. It calls only the earnings list (one request) and chooses no event for the reader.
 */
export async function EventFinder({ state }: { readonly state: EarningsListState }) {
  const result = await getEarningsPage(state.query);
  if (result.kind !== "ok") {
    return <ApiFailureState failure={result} subject="the earnings events" retryHref={state.canonical ?? RESEARCH_PATH} />;
  }
  const { items, total, limit, offset } = result.data;
  if (items.length === 0) {
    return (
      <ListEmpty
        total={total}
        rows={0}
        filtered={state.filtered}
        clearHref={RESEARCH_PATH}
        firstPageHref={earningsPageHref(state.query, 1, RESEARCH_PATH)}
        noun="stored observations"
        noData={{ title: "No earnings events are stored", body: "A study starts from a stored earnings event. Events appear here after ingestion has loaded them." }}
        noMatch={{ title: "Nothing matches these filters", body: "No stored earnings observation matches. Clear the filters to see every stored one." }}
      />
    );
  }
  return (
    <>
      <DataTable caption="Stored earnings events" columns={COLUMNS} rows={items} rowKey={(event) => event.id} />
      <Pagination
        total={total}
        limit={limit}
        offset={offset}
        maxPage={MAX_PAGE}
        hrefFor={(page) => earningsPageHref(state.query, page, RESEARCH_PATH)}
        noun="stored observations"
      />
    </>
  );
}
