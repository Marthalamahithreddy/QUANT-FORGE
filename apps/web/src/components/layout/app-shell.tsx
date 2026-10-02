import type { ReactNode } from "react";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import type { BackendStatus } from "@/types/api";

export interface AppShellProps {
  /** Resolved on the server before render; never inferred in the browser. */
  readonly status: BackendStatus;
  readonly children: ReactNode;
}

/**
 * Application chrome shared by every route.
 *
 * Presentational only: it performs no I/O, so the backend status is resolved by
 * the caller and passed in. This keeps the shell trivially testable.
 */
export function AppShell({ status, children }: AppShellProps) {
  return (
    <div className="min-h-dvh bg-surface-0 lg:grid lg:grid-cols-[15rem_minmax(0,1fr)]">
      <Sidebar />
      <div className="flex min-h-dvh min-w-0 flex-col lg:min-h-0">
        <Topbar status={status} />
        <main
          id="main-content"
          className="min-w-0 flex-1 px-4 py-6 sm:px-6 sm:py-8"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
