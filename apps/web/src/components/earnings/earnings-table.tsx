"use client";

import Link from "next/link";
import { useId, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  BASIS_LABEL,
  PIT_PRESENTATION,
  TIMING_LABEL,
} from "@/lib/availability";
import {
  matchesFilters,
  sortEvents,
  tickersAtEvent,
  type PitFilter,
  type SortDirection,
  type SortKey,
} from "@/lib/earnings-view";
import {
  MISSING,
  formatDecimal,
  formatRatioAsPercent,
  formatSignedDecimal,
  formatUtcInstant,
} from "@/lib/format";
import type { EarningsEvent } from "@/types/earnings";

interface Column {
  readonly key: SortKey | null;
  readonly label: string;
  readonly numeric?: boolean;
}

const COLUMNS: readonly Column[] = [
  { key: "event_date", label: "Event date" },
  { key: "company", label: "Company" },
  { key: "ticker", label: "Ticker" },
  { key: "timing", label: "Timing" },
  { key: "reported_eps", label: "Reported EPS", numeric: true },
  { key: "estimated_eps", label: "Est. EPS", numeric: true },
  { key: "surprise", label: "EPS surprise", numeric: true },
  { key: "source", label: "Source" },
  { key: "observed_at", label: "Observed at" },
  { key: null, label: "Basis" },
  { key: null, label: "PIT status" },
];

const CELL = "px-3 py-1.5 align-top";

export interface EarningsTableProps {
  readonly events: readonly EarningsEvent[];
}

/**
 * Research table over one loaded page of events.
 *
 * Sorting and filtering are local and apply to the rows already fetched; the
 * caption says so. Values are the backend's strings; PIT status is the
 * backend's classification.
 */
export function EarningsTable({ events }: EarningsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("event_date");
  const [direction, setDirection] = useState<SortDirection>("desc");
  const [query, setQuery] = useState("");
  const [pit, setPit] = useState<PitFilter>("all");
  const searchId = useId();
  const pitId = useId();

  const rows = useMemo(
    () =>
      sortEvents(
        events.filter((event) => matchesFilters(event, query, pit)),
        sortKey,
        direction,
      ),
    [events, query, pit, sortKey, direction],
  );

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setDirection((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setDirection("asc");
    }
  }

  return (
    <section aria-label="Earnings events" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={searchId} className="text-xs text-text-muted">
            Filter loaded rows
          </label>
          <input
            id={searchId}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Company, ticker or source"
            className="w-64 rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-sm text-text-primary placeholder:text-text-muted"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={pitId} className="text-xs text-text-muted">
            PIT status
          </label>
          <select
            id={pitId}
            value={pit}
            onChange={(e) => setPit(e.target.value as PitFilter)}
            className="rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-sm text-text-primary"
          >
            <option value="all">All</option>
            <option value="eligible">PIT eligible</option>
            <option value="ineligible">Not PIT eligible</option>
          </select>
        </div>
        <p className="ml-auto text-xs text-text-muted" aria-live="polite">
          Showing {rows.length} of {events.length} loaded rows · sorting and filtering apply to
          this page only
        </p>
      </div>

      {rows.length === 0 ? (
        <p role="status" className="rounded-md border border-dashed border-border-strong p-4 text-sm text-text-secondary">
          No loaded rows match this filter.
        </p>
      ) : (
        <div
          // Focusable so keyboard users can scroll the rows and wide columns.
          tabIndex={0}
          role="region"
          aria-label="Earnings table, scrollable"
          className="max-h-[70vh] overflow-auto rounded-md border border-border-subtle"
        >
          <table className="font-tabular w-full border-collapse text-xs">
            <caption className="sr-only">
              Earnings events. Sorting and filtering apply to the loaded page only.
            </caption>
            <thead className="sticky top-0 z-10 bg-surface-2">
              <tr className="border-b border-border-strong text-left text-text-muted">
                {COLUMNS.map((column) => {
                  const active = column.key !== null && column.key === sortKey;
                  return (
                    <th
                      key={column.label}
                      scope="col"
                      aria-sort={
                        active ? (direction === "asc" ? "ascending" : "descending") : undefined
                      }
                      className={`px-3 py-2 font-medium whitespace-nowrap ${
                        column.numeric ? "text-right" : ""
                      }`}
                    >
                      {column.key === null ? (
                        column.label
                      ) : (
                        <button
                          type="button"
                          onClick={() => toggleSort(column.key as SortKey)}
                          className="inline-flex items-center gap-1 rounded hover:text-text-primary"
                        >
                          {column.label}
                          <span aria-hidden="true" className="w-2 text-accent">
                            {active ? (direction === "asc" ? "▲" : "▼") : ""}
                          </span>
                        </button>
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((event) => (
                <EventRow key={event.id} event={event} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function EventRow({ event }: { readonly event: EarningsEvent }) {
  const { availability, eps_surprise: surprise } = event;
  const tickers = tickersAtEvent(event);
  const pit = PIT_PRESENTATION[availability.pit_eligibility];
  const period =
    event.fiscal_quarter === null
      ? `FY${event.fiscal_year}`
      : `FY${event.fiscal_year} Q${event.fiscal_quarter}`;

  return (
    <tr className="border-b border-border-subtle hover:bg-surface-1">
      <td className={`${CELL} whitespace-nowrap`}>
        <Link
          href={`/earnings/${event.id}`}
          className="font-medium text-accent underline-offset-2 hover:underline"
        >
          {event.event_date}
        </Link>
        <div className="text-text-muted">{period}</div>
      </td>
      <td className={`${CELL} min-w-40`}>
        <div className="text-text-primary">{event.company.display_name}</div>
        {event.company.cik ? (
          <div className="text-text-muted">CIK {event.company.cik}</div>
        ) : null}
      </td>
      <td className={`${CELL} font-mono whitespace-nowrap`}>
        {tickers.length > 0 ? (
          tickers.join(", ")
        ) : (
          <span className="text-text-muted" title="No listing was valid on the event date">
            Not resolved
          </span>
        )}
      </td>
      <td className={`${CELL} whitespace-nowrap text-text-secondary`}>
        {TIMING_LABEL[event.timing]}
      </td>
      <td className={`${CELL} text-right font-mono`}>{formatDecimal(event.reported_eps)}</td>
      <td className={`${CELL} text-right font-mono`}>{formatDecimal(event.estimated_eps)}</td>
      <td className={`${CELL} text-right font-mono whitespace-nowrap`}>
        {surprise.absolute === null ? (
          <span className="text-text-muted" title={surprise.reason}>
            {MISSING}
          </span>
        ) : (
          <>
            <div>{formatSignedDecimal(surprise.absolute)}</div>
            <div className="text-text-muted" title={surprise.reason}>
              {formatRatioAsPercent(surprise.ratio)}
            </div>
          </>
        )}
      </td>
      <td className={`${CELL} whitespace-nowrap text-text-secondary`}>{event.source.name}</td>
      <td className={`${CELL} font-mono whitespace-nowrap`}>
        {availability.observed_at === null ? (
          <span className="font-sans text-text-muted">Not disclosed</span>
        ) : (
          formatUtcInstant(availability.observed_at)
        )}
      </td>
      <td className={`${CELL} whitespace-nowrap text-text-secondary`}>
        {availability.observed_at_basis === null
          ? MISSING
          : BASIS_LABEL[availability.observed_at_basis]}
      </td>
      <td className={CELL}>
        <Badge tone={pit.tone}>{pit.label}</Badge>
        <div className="mt-0.5 text-text-muted">{pit.short}</div>
      </td>
    </tr>
  );
}
