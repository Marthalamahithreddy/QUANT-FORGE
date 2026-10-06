"use client";

import { usePathname } from "next/navigation";

import { NAV_GROUPS, activeNavItem } from "@/lib/navigation";

/**
 * Where the reader is, as plain text ("Evidence / Earnings"), in the header strip.
 *
 * The shell lives in the shared layout and cannot know a page's data, so this reads only the
 * location (`usePathname`). A detail page's own title and breadcrumb trail are page content.
 */
export function HeaderContext() {
  const item = activeNavItem(usePathname());
  if (item === null) return null;
  const group = NAV_GROUPS.find((candidate) => candidate.items.some((entry) => entry.id === item.id));
  return (
    <p className="type-meta truncate" data-testid="header-context">
      {group?.label ? <span>{group.label} / </span> : null}
      <span className="text-text-2">{item.label}</span>
    </p>
  );
}
