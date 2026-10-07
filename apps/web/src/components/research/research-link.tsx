import { AppLink } from "@/components/ui/app-link";

import { researchHref } from "@/lib/research-view";

/**
 * The compact link from a stored event into the Research workspace, worded and named the same
 * everywhere a list row offers it. It carries the event identity only: no benchmark, filing or
 * horizons are chosen by following it.
 */
export function ResearchLink({
  eventId,
  company,
  eventDate,
}: {
  readonly eventId: string;
  readonly company: string;
  readonly eventDate: string;
}) {
  return (
    <AppLink
      href={researchHref(eventId)}
      aria-label={`Research ${company}, ${eventDate}`}
      className="text-accent underline-offset-2 hover:underline"
    >
      Research
    </AppLink>
  );
}
