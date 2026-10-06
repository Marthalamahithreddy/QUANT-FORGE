import type { ReactNode } from "react";

import { Header } from "@/components/layout/header";
import { NavGroups } from "@/components/layout/nav-groups";
import { Wordmark } from "@/components/layout/wordmark";

export interface AppShellProps {
  /**
   * The API status chip. The layout passes a streamed element (a `Suspense` boundary around the
   * health read), so the shell paints without waiting for the API and a failed health read
   * never blanks the chrome.
   */
  readonly status: ReactNode;
  readonly children: ReactNode;
}

/**
 * Application chrome (spec section 6), rendered ONCE by the root layout.
 *
 * Because it lives in the layout, navigating between routes keeps it mounted: the rail, the
 * header strip and the status chip are not recreated and cannot flicker. Desktop: a 12.5rem rail,
 * a 44px header strip and the main region. Below 1024px the rail becomes a menu in the header.
 * Presentational and server-rendered: it performs no I/O, and only the nav links, the section
 * label, the mobile drawer and the theme control are Client Components.
 */
export function AppShell({ status, children }: AppShellProps) {
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
        <Header status={status} />
        <main id="main-content" className="min-w-0 flex-1 px-4 py-5 sm:px-6">
          {children}
        </main>
      </div>
    </div>
  );
}
