import { AppLink } from "@/components/ui/app-link";
import type { ReactNode } from "react";
import { Suspense } from "react";

import { Section } from "@/components/ui/section";
import { TableSkeleton } from "@/components/ui/table-skeleton";

/** How many rows a launch block shows. A teaser of the list, never the list. */
export const OVERVIEW_ROWS = 5;

export interface OverviewBlockProps {
  readonly title: string;
  /** What the rows are and how they are ordered: the API's order, stated, never re-sorted. */
  readonly note: string;
  /** Announced while the block loads ("Loading the latest earnings events"). */
  readonly loadingLabel: string;
  readonly children: ReactNode;
}

/**
 * One independent region of the Overview. Its heading paints at once; its data streams in behind
 * it in its own `Suspense` boundary, so a slow or failed source never holds up, or blanks, any
 * other region of the page.
 */
export function OverviewBlock({ title, note, loadingLabel, children }: OverviewBlockProps) {
  return (
    <Section title={title} note={note}>
      <Suspense fallback={<TableSkeleton label={loadingLabel} rows={5} />}>{children}</Suspense>
    </Section>
  );
}

/** The one way out of a block into its full surface. Always a real route. */
export function BlockLink({ href, children }: { readonly href: string; readonly children: ReactNode }) {
  return (
    <p className="text-sm">
      <AppLink href={href} className="text-accent hover:underline">
        {children}
      </AppLink>
    </p>
  );
}
