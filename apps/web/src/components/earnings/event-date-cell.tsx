import { AppLink } from "@/components/ui/app-link";
import type { EarningsEvent } from "@/lib/api/schema";
import { fiscalPeriod } from "@/lib/earnings-view";

/**
 * The identity cell of an earnings row: the event date as the link to the stored event, with its
 * fiscal period beneath. One component for every list that shows an event (Earnings, Overview), so
 * the link is named, styled and worded the same way everywhere.
 */
export function EventDateCell({ event }: { readonly event: EarningsEvent }) {
  return (
    <>
      <AppLink
        href={`/earnings/${event.id}`}
        aria-label={`Earnings event ${event.company.display_name}, ${event.event_date}`}
        className="font-medium text-accent underline-offset-2 hover:underline"
      >
        {event.event_date}
      </AppLink>
      <div className="type-meta">{fiscalPeriod(event)}</div>
    </>
  );
}
