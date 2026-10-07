import { AppLink } from "@/components/ui/app-link";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

export interface FilterBarProps {
  /** Where the GET form submits: the list's own path. */
  readonly action: string;
  /** Accessible name of the form landmark ("Filter earnings"). */
  readonly label: string;
  /** The reset link target; shown only when set (i.e. when a filter is active). */
  readonly clearHref?: string;
  /** Hidden inputs that must survive an Apply (none for lists today). */
  readonly children: ReactNode;
}

/**
 * A server-driven filter form: only the filters the endpoint actually supports, as plain GET
 * fields. Apply navigates to a canonical URL (filters in the query string, page reset), so a
 * filtered view is linkable, refreshable and back/forward-safe. There is no client state, no
 * debounce and no query builder.
 */
export function FilterBar({ action, label, clearHref, children }: FilterBarProps) {
  return (
    <form method="get" action={action} aria-label={label} className="flex flex-wrap items-end gap-3">
      {children}
      <Button type="submit">Apply</Button>
      {clearHref ? (
        <AppLink href={clearHref} className="text-sm text-accent hover:underline max-lg:flex max-lg:min-h-11 max-lg:items-center">
          Clear
        </AppLink>
      ) : null}
    </form>
  );
}
