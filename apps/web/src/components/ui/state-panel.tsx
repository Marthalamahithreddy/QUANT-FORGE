import type { ReactNode } from "react";

import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";

export interface StatePanelProps {
  readonly kind: "empty" | "error";
  readonly title: string;
  readonly children?: ReactNode;
}

/**
 * Compatibility wrapper: existing pages keep calling `StatePanel` and inherit the V2 primitives.
 * New code uses `EmptyState` / `ErrorState` directly; this is removed when the pages that use it
 * migrate (11.1C).
 */
export function StatePanel({ kind, title, children }: StatePanelProps) {
  return kind === "error" ? (
    <ErrorState title={title}>{children}</ErrorState>
  ) : (
    <EmptyState title={title}>{children}</EmptyState>
  );
}
