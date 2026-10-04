import type { ReactNode } from "react";

import { Header } from "@/components/layout/header";
import { NavGroups } from "@/components/layout/nav-groups";
import { Wordmark } from "@/components/layout/wordmark";
import type { Crumb } from "@/components/ui/breadcrumbs";
import type { BackendStatus } from "@/types/api";

export interface AppShellProps {
  /**
   * Resolved on the server by the caller and passed in; never inferred in the browser.
   * `undefined` renders "Checking API", which is how the shell paints before any answer exists
   * (the route `loading.tsx` uses it). Streaming the real status is route-data work (11.1C).
   */
  readonly status?: BackendStatus;
  /** Detail routes only (spec section 5.5). */
  readonly breadcrumbs?: readonly Crumb[];
  readonly children: ReactNode;
}

/**
 * Application chrome shared by every route (spec section 6).
 *
 * Desktop: a 12.5rem rail, a 44px header strip, and the main region. Below 1024px the rail
 * becomes a menu in the header strip. Presentational and server-rendered: it performs no I/O, and
 * only the nav links, the mobile drawer and the theme control are Client Components.
 */
export function AppShell({ status, breadcrumbs, children }: AppShellProps) {
  return (
    <div className="min-h-dvh bg-bg lg:grid lg:grid-cols-[12.5rem_minmax(0,1fr)]">
      <aside className="hidden border-r border-border-subtle bg-surface lg:block">
        <div className="sticky top-0 flex h-dvh flex-col gap-4 p-3">
          <Wordmark />
          <nav aria-label="Primary">
            <NavGroups />
          </nav>
        </div>
      </aside>
      <div className="flex min-h-dvh min-w-0 flex-col lg:min-h-0">
        <Header status={status} breadcrumbs={breadcrumbs} />
        <main id="main-content" className="min-w-0 flex-1 px-4 py-5 sm:px-6">
          {children}
        </main>
      </div>
    </div>
  );
}
