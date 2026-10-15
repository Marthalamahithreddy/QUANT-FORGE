/**
 * Primary navigation model (design spec section 5.2).
 *
 *   Overview
 *   Evidence   -> Companies, Earnings, Filings
 *   Research   -> Research, Saved research
 *
 * Every entry is a real route. There are no "planned" rows: a section that does not exist is not
 * shown (Backtests have no route and no entry).
 */

export interface NavItem {
  readonly id: string;
  readonly label: string;
  readonly href: string;
}

export interface NavGroup {
  readonly id: string;
  /** `null` for the ungrouped top entry. */
  readonly label: string | null;
  readonly items: readonly NavItem[];
}

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    id: "overview",
    label: null,
    items: [
      {
        id: "overview",
        label: "Overview",
        href: "/",
      },
    ],
  },
  {
    id: "evidence",
    label: "Evidence",
    items: [
      {
        id: "companies",
        label: "Companies",
        href: "/companies",
      },
      {
        id: "earnings",
        label: "Earnings",
        href: "/earnings",
      },
      {
        id: "filings",
        label: "Filings",
        href: "/filings",
      },
    ],
  },
  {
    id: "research",
    label: "Research",
    items: [
      {
        id: "research",
        label: "Research",
        href: "/research",
      },
      {
        id: "saved",
        label: "Saved research",
        href: "/research/saved",
      },
    ],
  },
];

export const NAV_ITEMS: readonly NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

/** Whether `pathname` is within the section rooted at `href`. */
export function isActivePath(pathname: string, href: string): boolean {
  if (href === "/") {
    return pathname === "/";
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The single entry that is current for `pathname`: the MOST SPECIFIC match, so `/research/saved`
 * marks "Saved research" and not also "Research".
 */
export function activeNavItem(pathname: string): NavItem | null {
  let best: NavItem | null = null;
  for (const item of NAV_ITEMS) {
    if (isActivePath(pathname, item.href) && (best === null || item.href.length > best.href.length)) {
      best = item;
    }
  }
  return best;
}
