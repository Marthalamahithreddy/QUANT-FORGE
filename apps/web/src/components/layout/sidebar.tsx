"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS, isActivePath } from "@/lib/navigation";

/**
 * Primary navigation.
 *
 * A client component only because the active section depends on the browser
 * location. Planned sections are inert, labelled rows rather than links, so no
 * control implies a route that does not exist yet.
 */
export function Sidebar() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Primary"
      className="border-b border-border-subtle bg-surface-1 lg:border-r lg:border-b-0"
    >
      <div className="flex flex-col gap-3 p-3 lg:sticky lg:top-0 lg:h-dvh lg:gap-4 lg:p-4">
        <div className="hidden items-baseline gap-2 px-2 lg:flex">
          <span className="font-mono text-sm font-semibold tracking-tight text-text-primary">
            QF<span className="text-accent">/</span>
          </span>
          <span className="text-xs tracking-[0.14em] text-text-muted uppercase">
            Workspace
          </span>
        </div>

        <ul className="flex gap-1 overflow-x-auto lg:flex-col lg:gap-0.5 lg:overflow-visible">
          {NAV_ITEMS.map((item) => {
            const href = item.status === "available" ? item.href : null;
            if (href === null) {
              return (
                <li key={item.id} className="shrink-0 lg:shrink">
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
                </li>
              );
            }
            const active = isActivePath(pathname, href);
            return (
              <li key={item.id} className="shrink-0 lg:shrink">
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={
                    active
                      ? "flex items-center rounded-md border border-accent-subtle bg-accent-subtle/60 px-3 py-2 text-sm font-medium text-text-primary"
                      : "flex items-center rounded-md border border-transparent px-3 py-2 text-sm text-text-secondary hover:bg-surface-2 hover:text-text-primary"
                  }
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>

        <p className="mt-auto hidden px-2 text-xs leading-relaxed text-text-muted lg:block">
          Sections marked <span className="font-mono uppercase">planned</span> are not
          implemented.
        </p>
      </div>
    </nav>
  );
}
