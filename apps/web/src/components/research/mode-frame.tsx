import type { ReactNode } from "react";

import { StatusBadge } from "@/components/state/status-badge";
import type { ResearchMode } from "@/lib/api/schema";
import type { StateKind } from "@/lib/state";

const MODE = {
  DESCRIPTIVE: { label: "DESCRIPTIVE", rule: "[border-left-style:solid] border-l-mode-descriptive", text: "text-mode-descriptive", hue: "descriptive" as const },
  POINT_IN_TIME: { label: "POINT-IN-TIME", rule: "[border-left-style:dashed] border-l-mode-pit", text: "text-mode-pit", hue: "pit" as const },
};

/** The badge is both parts or neither: a state without its text (or the reverse) is a type error. */
type ModeBadge = { readonly state: StateKind; readonly stateText: string } | { readonly state?: undefined; readonly stateText?: undefined };

export type ModeFrameProps = ModeBadge & {
  readonly mode: ResearchMode;
  readonly id: string;
  /** The question this mode answers, in one line. */
  readonly question: string;
  readonly children: ReactNode;
};

/**
 * One of the two analytical modes, unmistakable and distinguishable in greyscale: the label
 * (DESCRIPTIVE / POINT-IN-TIME), a SOLID left rule versus a DASHED one, the mode hue, and a state
 * badge that repeats the status in words. The two modes never share a frame, and a refusal in one
 * never dims, collapses or reorders the other.
 */
export function ModeFrame({ mode, id, question, state, stateText, children }: ModeFrameProps) {
  const meta = MODE[mode];
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      data-mode={mode}
      className={`scroll-mt-14 rounded border border-border bg-surface ${meta.rule} border-l-[3px]`}
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border-subtle px-3 py-2">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h2 id={`${id}-title`} className={`type-section ${meta.text}`}>
            {meta.label}
          </h2>
          <p className="type-meta">{question}</p>
        </div>
        {state !== undefined && stateText !== undefined ? (
          <StatusBadge kind={state} mode={meta.hue}>
            {stateText}
          </StatusBadge>
        ) : null}
      </header>
      <div className="flex flex-col gap-4 p-3">{children}</div>
    </section>
  );
}
