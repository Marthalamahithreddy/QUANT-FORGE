/**
 * Primary navigation model.
 *
 * Only Overview exists at the foundation milestone. The remaining entries are
 * declared so the information architecture is settled, and are explicitly
 * marked `planned` so the UI can present them as unavailable rather than
 * linking to routes that do not exist.
 */

export type NavItemStatus = "available" | "planned";

export interface NavItem {
  readonly id: string;
  readonly label: string;
  readonly summary: string;
  /** `null` until the route exists; unavailable items are never rendered as links. */
  readonly href: string | null;
  readonly status: NavItemStatus;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: "overview",
    label: "Overview",
    summary: "Platform status and backend connectivity.",
    href: "/",
    status: "available",
  },
  {
    id: "research",
    label: "Research",
    summary: "Filings, transcripts and event notes.",
    href: null,
    status: "planned",
  },
  {
    id: "companies",
    label: "Companies",
    summary: "Issuer reference data and fundamentals.",
    href: null,
    status: "planned",
  },
  {
    id: "events",
    label: "Events",
    summary: "Earnings calendar and event timelines.",
    href: null,
    status: "planned",
  },
  {
    id: "signals",
    label: "Signals",
    summary: "Factor and event-signal definitions.",
    href: null,
    status: "planned",
  },
  {
    id: "backtests",
    label: "Backtests",
    summary: "Event studies and strategy runs.",
    href: null,
    status: "planned",
  },
];
