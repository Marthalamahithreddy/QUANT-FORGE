import { useId, type ReactNode } from "react";

export interface SectionProps {
  /** Anchor target, for in-page links. */
  readonly id?: string;
  readonly title: string;
  readonly note?: ReactNode;
  readonly children: ReactNode;
}

/**
 * The DEFAULT grouping: a rule and a section label, no box. A region that needs a title AND a
 * state uses `Frame` instead; most regions are not frames.
 */
export function Section({ id, title, note, children }: SectionProps) {
  const headingId = useId();
  return (
    <section id={id} aria-labelledby={headingId} className="flex scroll-mt-14 flex-col gap-3 border-t border-border-subtle pt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id={headingId} className="type-section">
          {title}
        </h2>
        {note ? <p className="type-meta">{note}</p> : null}
      </div>
      {children}
    </section>
  );
}
