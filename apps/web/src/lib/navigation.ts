/**
 * Primary navigation model.
 *
 * An entry is `available` only when its route exists and shows real backend
 * data (or, for Research, an honest account of what is implemented). Planned
 * entries have `href: null` and are rendered as inert, labelled rows, never as
 * links to routes that do not exist.
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
    summary: "System state and data coverage.",
    href: "/",
    status: "available",
  },
  {
    id: "companies",
    label: "Companies",
    summary: "Issuer directory. Needs a read-only companies API.",
    href: null,
    status: "planned",
  },
  {
    id: "earnings",
    label: "Earnings",
    summary: "Earnings events with availability evidence.",
    href: "/earnings",
    status: "available",
  },
  {
    id: "research",
    label: "Research",
    summary: "What the research engine implements today.",
    href: "/research",
    status: "available",
  },
  {
    id: "backtests",
    label: "Backtests",
    summary: "Not implemented.",
    href: null,
    status: "planned",
  },
];

/** Whether `pathname` is within the section rooted at `href`. */
export function isActivePath(pathname: string, href: string): boolean {
  if (href === "/") {
    return pathname === "/";
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}
