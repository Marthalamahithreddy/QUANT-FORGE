import { useId, type ReactNode } from "react";

export interface FrameProps {
  /** Anchor target, for in-page links. */
  readonly id?: string;
  readonly title: string;
  readonly note?: ReactNode;
  /** Right-aligned header content, typically a status badge. */
  readonly actions?: ReactNode;
  readonly children: ReactNode;
}

/**
 * A bordered block with a header strip, for a region that needs a title and a state (evidence
 * panels, Overview panels). 4px radius, no shadow. Frames are never nested inside frames.
 */
export function Frame({ id, title, note, actions, children }: FrameProps) {
  const headingId = useId();
  return (
    <section id={id} aria-labelledby={headingId} className="scroll-mt-14 rounded border border-border bg-surface">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border-subtle px-3 py-2">
        <h2 id={headingId} className="type-section">
          {title}
        </h2>
        {note ? <p className="type-meta">{note}</p> : null}
        {actions ? <div className="ml-auto flex items-center gap-2">{actions}</div> : null}
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}
