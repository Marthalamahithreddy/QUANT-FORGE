import { compareDecimalStrings } from "@/lib/format";
import type { EarningsEvent } from "@/types/earnings";

/**
 * Tickers whose listing the backend marked valid on the event date. Empty when
 * none was, which the UI shows as "Not resolved" rather than guessing from a
 * listing that was valid at some other time.
 */
export function tickersAtEvent(event: EarningsEvent): readonly string[] {
  return event.listings.filter((l) => l.valid_on_event_date).map((l) => l.ticker);
}

export type SortKey =
  | "event_date"
  | "company"
  | "ticker"
  | "timing"
  | "reported_eps"
  | "estimated_eps"
  | "surprise"
  | "source"
  | "observed_at";

export type SortDirection = "asc" | "desc";

const text = (a: string, b: string): number => a.localeCompare(b, "en");

/** `null` always sorts last regardless of direction (handled by the caller). */
function compareNullable<T>(a: T | null, b: T | null, cmp: (x: T, y: T) => number): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return cmp(a, b);
}

function compareBy(key: SortKey, a: EarningsEvent, b: EarningsEvent): number {
  switch (key) {
    case "event_date":
      return text(a.event_date, b.event_date);
    case "company":
      return text(a.company.display_name, b.company.display_name);
    case "ticker":
      return compareNullable(
        tickersAtEvent(a)[0] ?? null,
        tickersAtEvent(b)[0] ?? null,
        text,
      );
    case "timing":
      return text(a.timing, b.timing);
    case "reported_eps":
      return compareDecimalStrings(a.reported_eps, b.reported_eps);
    case "estimated_eps":
      return compareDecimalStrings(a.estimated_eps, b.estimated_eps);
    case "surprise":
      return compareDecimalStrings(a.eps_surprise.absolute, b.eps_surprise.absolute);
    case "source":
      return text(a.source.name, b.source.name);
    case "observed_at":
      // ISO-8601 UTC strings of equal shape order lexically; null sorts last.
      return compareNullable(
        a.availability.observed_at === null ? null : Date.parse(a.availability.observed_at),
        b.availability.observed_at === null ? null : Date.parse(b.availability.observed_at),
        (x, y) => x - y,
      );
  }
}

const NULLS_LAST: ReadonlySet<SortKey> = new Set([
  "ticker",
  "reported_eps",
  "estimated_eps",
  "surprise",
  "observed_at",
]);

/** Stable sort that keeps missing values at the bottom in both directions. */
export function sortEvents(
  events: readonly EarningsEvent[],
  key: SortKey,
  direction: SortDirection,
): EarningsEvent[] {
  const sign = direction === "asc" ? 1 : -1;
  const isMissing = (event: EarningsEvent): boolean => {
    switch (key) {
      case "ticker":
        return tickersAtEvent(event).length === 0;
      case "reported_eps":
        return event.reported_eps === null;
      case "estimated_eps":
        return event.estimated_eps === null;
      case "surprise":
        return event.eps_surprise.absolute === null;
      case "observed_at":
        return event.availability.observed_at === null;
      default:
        return false;
    }
  };
  return events
    .map((event, index) => ({ event, index }))
    .sort((x, y) => {
      if (NULLS_LAST.has(key)) {
        const mx = isMissing(x.event);
        const my = isMissing(y.event);
        if (mx !== my) return mx ? 1 : -1;
        if (mx && my) return x.index - y.index;
      }
      return sign * compareBy(key, x.event, y.event) || x.index - y.index;
    })
    .map((entry) => entry.event);
}

export type PitFilter = "all" | "eligible" | "ineligible";

export function matchesFilters(
  event: EarningsEvent,
  query: string,
  pit: PitFilter,
): boolean {
  if (pit === "eligible" && event.availability.pit_eligibility !== "ELIGIBLE") return false;
  if (pit === "ineligible" && !event.availability.pit_eligibility.startsWith("INELIGIBLE")) {
    return false;
  }
  const needle = query.trim().toLowerCase();
  if (needle === "") return true;
  const haystack = [
    event.company.display_name,
    event.company.legal_name,
    event.source.name,
    // Same tickers the table displays: valid on the event date, never a current one.
    ...tickersAtEvent(event),
  ];
  return haystack.some((h) => h.toLowerCase().includes(needle));
}
