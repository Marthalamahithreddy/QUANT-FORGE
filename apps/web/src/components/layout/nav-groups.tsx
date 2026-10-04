import { useId } from "react";

import { NavLink } from "@/components/layout/nav-link";
import { NAV_GROUPS } from "@/lib/navigation";

/**
 * The grouped navigation list (Overview / Evidence / Research). A Server Component: only each
 * `NavLink` is client-side. Rendered in the desktop rail and inside the mobile drawer.
 */
export function NavGroups() {
  // The list renders in the rail AND in the drawer: ids must be unique per instance.
  const prefix = useId();
  return (
    <ul className="flex flex-col gap-4">
      {NAV_GROUPS.map((group) => (
        <li key={group.id}>
          {group.label === null ? null : (
            <p id={`${prefix}-${group.id}`} className="type-section mb-1 px-3">
              {group.label}
            </p>
          )}
          <ul
            aria-labelledby={group.label === null ? undefined : `${prefix}-${group.id}`}
            className="flex flex-col gap-0.5"
          >
            {group.items.map((item) => (
              <li key={item.id}>
                <NavLink item={item} />
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}
