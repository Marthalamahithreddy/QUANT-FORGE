import { AppLink } from "@/components/ui/app-link";

import { formatGroupedDecimal } from "@/lib/format";

export interface PaginationProps {
  /** Exactly what the API reported for this page. */
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  /** Highest page number the URL accepts; Next stops there even if `total` allows more. */
  readonly maxPage: number;
  /** The canonical URL of a page number (1-based). Defaults are omitted by the caller. */
  readonly hrefFor: (page: number) => string;
  /** What the rows are, plural ("stored observations"). */
  readonly noun: string;
}

const LINK = "text-accent hover:underline max-lg:flex max-lg:min-h-11 max-lg:items-center";
const DISABLED = "text-text-3 max-lg:flex max-lg:min-h-11 max-lg:items-center";

const count = (value: number): string => formatGroupedDecimal(String(value));

/**
 * Offset paging as the API defines it: `total`, `limit`, `offset`. No cursors and no invented
 * semantics. The links are plain anchors to canonical URLs (back/forward and refresh just work),
 * a boundary is a disabled label rather than a dead link, and the position is stated in words.
 */
export function Pagination({ total, limit, offset, maxPage, hrefFor, noun }: PaginationProps) {
  const first = offset + 1;
  const last = Math.min(offset + limit, total);
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  const hasPrevious = page > 1;
  const hasNext = last < total && page < maxPage;

  return (
    <nav aria-label="Pagination" className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
      <p className="type-meta" data-testid="pagination-position">
        Rows {count(first)}–{count(last)} of {count(total)} {noun}
      </p>
      <p className="type-meta">
        Page {count(page)} of {count(pages)}
      </p>
      <div className="flex items-center gap-4">
        {hasPrevious ? (
          <AppLink href={hrefFor(page - 1)} rel="prev" className={LINK}>
            Previous page
          </AppLink>
        ) : (
          <span aria-disabled="true" className={DISABLED}>
            Previous page
          </span>
        )}
        {hasNext ? (
          <AppLink href={hrefFor(page + 1)} rel="next" className={LINK}>
            Next page
          </AppLink>
        ) : (
          <span aria-disabled="true" className={DISABLED}>
            Next page
          </span>
        )}
      </div>
    </nav>
  );
}
