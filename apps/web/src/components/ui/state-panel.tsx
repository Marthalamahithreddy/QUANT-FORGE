import type { ReactNode } from "react";

export interface StatePanelProps {
  readonly kind: "empty" | "error";
  readonly title: string;
  readonly children?: ReactNode;
}

/**
 * Intentional empty and failure states. Errors use `role="alert"`; an empty
 * state is informational, not an error, and is announced politely.
 */
export function StatePanel({ kind, title, children }: StatePanelProps) {
  return (
    <div
      role={kind === "error" ? "alert" : "status"}
      className={`rounded-md border border-dashed p-5 ${
        kind === "error" ? "border-status-down/50" : "border-border-strong"
      } bg-surface-1`}
    >
      <p className="text-sm font-medium text-text-primary">{title}</p>
      {children ? (
        <div className="mt-1.5 max-w-3xl text-sm leading-relaxed text-text-secondary">
          {children}
        </div>
      ) : null}
    </div>
  );
}
