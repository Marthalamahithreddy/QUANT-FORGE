import type { ReactNode } from "react";

export interface EmptyStateProps {
  readonly title: string;
  readonly children?: ReactNode;
  /** The one thing the reader can do next (a link or button), if any. */
  readonly action?: ReactNode;
}

/**
 * "Nothing is here", announced politely. An empty state is information, not a failure and not a
 * refusal: it uses a plain hairline (dashed and dotted outlines belong to the state system) and
 * never shows example rows. Page-specific wording belongs to the page.
 */
export function EmptyState({ title, children, action }: EmptyStateProps) {
  return (
    <div role="status" className="rounded border border-border-subtle bg-surface p-4">
      <p className="text-sm font-medium text-text">{title}</p>
      {children ? <div className="mt-1.5 max-w-3xl text-sm leading-relaxed text-text-2">{children}</div> : null}
      {action ? <div className="mt-3 text-sm">{action}</div> : null}
    </div>
  );
}
