import Link from "next/link";
import { Suspense } from "react";

import { DomainState } from "@/components/state/domain-state";
import { getEarningsEvent } from "@/lib/api/earnings";
import type { ApiResult } from "@/lib/api/result";
import type { EarningsEvent } from "@/lib/api/schema";
import { fiscalPeriod, tickersAtEvent } from "@/lib/earnings-view";
import { formatDate } from "@/lib/format";

/**
 * The CURRENT stored event record for one saved record, fetched for readability only. It is not
 * part of the baseline (a record's manifest does not carry a company name or ticker), and it is
 * labelled as current wherever it appears. The lookup is a promise made ONCE per distinct event by
 * the table (so records that share an event share one read, bounded by the page size); a record
 * whose event is gone (404) or unreadable shows that in this cell and nothing else changes.
 */
export function lookupEvents(eventIds: readonly string[]): ReadonlyMap<string, Promise<ApiResult<EarningsEvent>>> {
  const lookups = new Map<string, Promise<ApiResult<EarningsEvent>>>();
  for (const id of eventIds) if (!lookups.has(id)) lookups.set(id, getEarningsEvent(id));
  return lookups;
}

async function CurrentEvent({ lookup }: { readonly lookup: Promise<ApiResult<EarningsEvent>> }) {
  const result = await lookup;
  if (result.kind === "ok") {
    const event = result.data;
    const tickers = tickersAtEvent(event);
    return (
      <span className="flex flex-col gap-0.5">
        <Link href={`/earnings/${event.id}`} className="text-accent hover:underline">
          {event.company.display_name}
        </Link>
        <span className="type-meta">
          {tickers.length > 0 ? <span className="type-id">{tickers.join(", ")}</span> : "No ticker proven for the event date"} · {fiscalPeriod(event)} ·{" "}
          <span className="num">{formatDate(event.event_date)}</span>
        </span>
      </span>
    );
  }
  if (result.kind === "not_found") {
    return <DomainState kind="MISSING" reason="No stored event has this identifier now. The saved baseline is unaffected." />;
  }
  return <DomainState kind="UNAVAILABLE" reason="The current event record could not be read. The saved baseline is unaffected." />;
}

export function CurrentEventCell({ lookup }: { readonly lookup: Promise<ApiResult<EarningsEvent>> }) {
  return (
    <Suspense fallback={<span className="type-meta">Loading current event…</span>}>
      <CurrentEvent lookup={lookup} />
    </Suspense>
  );
}
