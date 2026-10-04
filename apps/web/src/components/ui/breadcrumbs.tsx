import Link from "next/link";

export interface Crumb {
  readonly label: string;
  /** Omitted for the current page. */
  readonly href?: string;
}

export interface BreadcrumbsProps {
  readonly items: readonly Crumb[];
}

/** Shown on detail routes only. The last item is the current page. */
export function Breadcrumbs({ items }: BreadcrumbsProps) {
  if (items.length === 0) return null;
  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-1.5 text-xs text-text-2">
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <li key={`${index}-${item.label}`} className={`flex min-w-0 items-center gap-1.5 ${last ? "" : "shrink-0"}`}>
              {index > 0 ? (
                <span aria-hidden="true" className="text-text-3">
                  /
                </span>
              ) : null}
              {last || item.href === undefined ? (
                <span aria-current={last ? "page" : undefined} className="truncate text-text">
                  {item.label}
                </span>
              ) : (
                <Link href={item.href} className="text-accent hover:underline">
                  {item.label}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
