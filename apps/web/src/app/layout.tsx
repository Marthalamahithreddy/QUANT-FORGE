import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Suspense } from "react";

import "./globals.css";

import { ApiStatus } from "@/components/layout/api-status";
import { AppShell } from "@/components/layout/app-shell";
import { LiveApiStatus } from "@/components/layout/live-api-status";
import { THEME_COOKIE, TINT_COOKIE, parseTheme, parseTint } from "@/lib/preferences";

export const metadata: Metadata = {
  title: { default: "QuantForge", template: "%s · QuantForge" },
  description: "Read-only equity research over provenance-tracked market data, SEC filings and financial facts.",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Presentation preferences are server-readable cookies, so the very first paint already has the
  // right theme (no flash) and no functional state lives in browser storage.
  const store = await cookies();
  const theme = parseTheme(store.get(THEME_COOKIE)?.value);
  const tint = parseTint(store.get(TINT_COOKIE)?.value);

  return (
    <html lang="en" data-theme={theme === "system" ? undefined : theme} data-tint={tint ? "on" : undefined}>
      <body className="antialiased">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:border focus:border-border-control focus:bg-surface-raised focus:px-3 focus:py-2 focus:text-sm focus:text-text"
        >
          Skip to content
        </a>
        <AppShell
          status={
            <Suspense fallback={<ApiStatus />}>
              <LiveApiStatus />
            </Suspense>
          }
        >
          {children}
        </AppShell>
      </body>
    </html>
  );
}
