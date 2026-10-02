import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "QuantForge",
  description: "Event-driven equity research and quantitative backtesting.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="antialiased">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:border focus:border-border-strong focus:bg-surface-2 focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
