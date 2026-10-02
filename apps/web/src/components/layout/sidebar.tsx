import Link from "next/link";

import { NAV_ITEMS } from "@/lib/navigation";

/**
 * Primary navigation.
 *
 * Planned sections are rendered as inert, clearly labelled rows rather than
 * links, so no control implies a route that does not exist yet.
 */
export function Sidebar() {
  return (
    <nav
      aria-label="Primary"
      className="border-b border-border-subtle bg-surface-1 lg:border-r lg:border-b-0"
    >
      <div className="flex flex-col gap-3 p-3 lg:h-full lg:gap-4 lg:p-4">
        <p className="hidden px-2 text-xs font-medium tracking-[0.14em] text-text-muted uppercase lg:block">
          Workspace
        </p>

        <ul className="flex gap-1 overflow-x-auto lg:flex-col lg:gap-0.5 lg:overflow-visible">
          {NAV_ITEMS.map((item) => {
            const isAvailable = item.status === "available" && item.href !== null;

            return (
              <li key={item.id} className="shrink-0 lg:shrink">
                {isAvailable ? (
                  <Link
                    href={item.href ?? "/"}
                    aria-current="page"
                    className="flex items-center gap-2 rounded-md border border-accent-subtle bg-accent-subtle/60 px-3 py-2 text-sm font-medium text-text-primary lg:border-transparent lg:bg-transparent lg:hover:bg-surface-2 lg:hover:text-text-primary"
                  >
                    <span>{item.label}</span>
                    <span className="sr-only">(current section)</span>
                  </Link>
                ) : (
                  <span
                    aria-disabled="true"
                    title={item.summary}
                    className="flex cursor-not-allowed items-center gap-2 rounded-md border border-transparent px-3 py-2 text-sm text-text-muted"
                  >
                    <span>{item.label}</span>
                    <span className="rounded border border-border-subtle bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-text-muted uppercase">
                      Planned
                    </span>
                  </span>
                )}
              </li>
            );
          })}
        </ul>

        <p className="mt-auto hidden px-2 text-xs leading-relaxed text-text-muted lg:block">
          Sections marked{" "}
          <span className="font-mono uppercase">planned</span> are not yet
          implemented.
        </p>
      </div>
    </nav>
  );
}
