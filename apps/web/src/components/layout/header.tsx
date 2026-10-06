import type { ReactNode } from "react";

import { HeaderContext } from "@/components/layout/header-context";
import { MobileNav } from "@/components/layout/mobile-nav";
import { NavGroups } from "@/components/layout/nav-groups";
import { ThemeControl } from "@/components/layout/theme-control";
import { Wordmark } from "@/components/layout/wordmark";

export interface HeaderProps {
  /** The API status chip, supplied by the layout (it streams; the header never waits for it). */
  readonly status: ReactNode;
}

/**
 * The 44px header strip: navigation button (below desktop), the current section, the theme
 * control and the API status. One row, no product-name repetition (the wordmark lives in the rail
 * and the drawer).
 */
export function Header({ status }: HeaderProps) {
  return (
    <header className="sticky top-0 z-20 flex h-11 items-center gap-3 border-b border-border-subtle bg-surface px-3 sm:px-4">
      <MobileNav>
        <div className="mb-3">
          <Wordmark />
        </div>
        <NavGroups />
        {/* Below `sm` the header has no room for the theme control: it lives here instead. */}
        <div className="mt-5 px-3 sm:hidden">
          <p className="type-section mb-1.5">Theme</p>
          <ThemeControl name="qf-theme-menu" />
        </div>
      </MobileNav>
      <div className="min-w-0 flex-1">
        <HeaderContext />
      </div>
      <div className="hidden sm:block">
        <ThemeControl />
      </div>
      {status}
    </header>
  );
}
