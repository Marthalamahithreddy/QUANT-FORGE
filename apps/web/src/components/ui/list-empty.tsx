import Link from "next/link";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/ui/empty-state";

export interface ListEmptyProps {
  readonly total: number;
  /** Rows returned for THIS page. */
  readonly rows: number;
  readonly filtered: boolean;
  readonly clearHref: string;
  readonly firstPageHref: string;
  /** What the rows are, plural ("stored observations"). */
  readonly noun: string;
  readonly noData: { readonly title: string; readonly body: ReactNode };
  readonly noMatch: { readonly title: string; readonly body: ReactNode };
}

/**
 * The three ways a list can show no rows, kept apart: nothing is stored, the filters matched
 * nothing, or the page is past the last result. `null` when there are rows to show.
 */
export function ListEmpty({ total, rows, filtered, clearHref, firstPageHref, noun, noData, noMatch }: ListEmptyProps) {
  if (total === 0) {
    return filtered ? (
      <EmptyState
        title={noMatch.title}
        action={
          <Link href={clearHref} className="text-accent hover:underline">
            Clear the filters
          </Link>
        }
      >
        {noMatch.body}
      </EmptyState>
    ) : (
      <EmptyState title={noData.title}>{noData.body}</EmptyState>
    );
  }
  if (rows === 0) {
    return (
      <EmptyState
        title="This page is past the last result"
        action={
          <Link href={firstPageHref} className="text-accent hover:underline">
            Go to the first page
          </Link>
        }
      >
        There are {total} {noun} for this view, and none at this position.
      </EmptyState>
    );
  }
  return null;
}
