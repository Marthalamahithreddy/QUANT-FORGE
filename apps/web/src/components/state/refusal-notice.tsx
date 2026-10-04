import type { ReactNode } from "react";

import { StateGlyph, type GlyphKind } from "@/components/state/glyphs";
import { TONE_TEXT } from "@/components/state/tone";
import { Callout } from "@/components/ui/callout";
import { STATE_META, type NonValueState } from "@/lib/state";

export interface RefusalNoticeProps {
  readonly kind: NonValueState;
  /** What section or value this replaces ("Benchmark return", "Point-in-time study"). */
  readonly title: string;
  /** The backend's reason, verbatim. */
  readonly reason?: string;
  readonly children?: ReactNode;
  /** The next step, if there is one. */
  readonly action?: ReactNode;
}

/**
 * The block form of a non-value state: it REPLACES the section body and leaves sibling sections
 * alone. A domain state is announced politely (`role="status"`); only `ERROR` is an alert. It is
 * deliberately not styled as a crash, and never hides the content around it.
 */
export function RefusalNotice({ kind, title, reason, children, action }: RefusalNoticeProps) {
  const meta = STATE_META[kind];
  const glyph: GlyphKind | null = kind === "NOT_APPLICABLE" ? null : (kind as GlyphKind);
  return (
    <Callout
      tone={meta.tone}
      rule={meta.border}
      role={kind === "ERROR" ? "alert" : "status"}
      label={`${title}: ${meta.label}`}
    >
      <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-medium">
        {glyph ? (
          <span className={TONE_TEXT[meta.tone]}>
            <StateGlyph kind={glyph} />
          </span>
        ) : null}
        <span>{title}</span>
        <span className={`text-xs font-medium ${TONE_TEXT[meta.tone]}`}>{meta.label}</span>
      </p>
      <p className="mt-1 text-text-2">{reason ?? meta.sentence}</p>
      {children ? <div className="mt-1 text-text-2">{children}</div> : null}
      {action ? <div className="mt-2 text-sm">{action}</div> : null}
    </Callout>
  );
}
