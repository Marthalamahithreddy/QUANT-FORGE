"use client";

import { usePathname } from "next/navigation";

import { AppLink } from "@/components/ui/app-link";
import { activeNavItem, type NavItem } from "@/lib/navigation";

/**
 * One navigation link. It is an `AppLink`, so it never prefetches (the same no-prefetch policy as
 * every other in-app link; a caller cannot switch it back on).
 *
 * A Client Component only because the current section depends on the browser location
 * (`usePathname`). The group structure around it stays on the server.
 */
export function NavLink({ item }: { readonly item: NavItem }) {
  const pathname = usePathname();
  const active = activeNavItem(pathname)?.id === item.id;
  return (
    <AppLink
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={`type-nav flex items-center rounded-md border px-3 py-1.5 max-lg:min-h-11 ${
        active
          ? "border-accent/40 bg-selected text-text"
          : "border-transparent text-text-2 hover:bg-hover hover:text-text"
      }`}
    >
      {item.label}
    </AppLink>
  );
}
