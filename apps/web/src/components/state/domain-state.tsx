import { StateGlyph, type GlyphKind } from "@/components/state/glyphs";
import { BORDER_STYLE, TONE_OUTLINE, TONE_TEXT } from "@/components/state/tone";
import { STATE_META, type NonValueState } from "@/lib/state";

export interface DomainStateProps {
  readonly kind: NonValueState;
  /**
   * The backend's own reason for this state, shown verbatim. It replaces the generic sentence in
   * the accessible description and the hover hint; the label is always the state's own word.
   */
  readonly reason?: string;
  /** The one thing the reader can do about it (only NOT_REQUESTED normally has one). */
  readonly action?: React.ReactNode;
  /** A dash-only presentation for dense cells. Honoured for NOT_STATED and NOT_APPLICABLE only. */
  readonly compact?: boolean;
}

/**
 * An inline domain state: a value that is not a value, said precisely.
 *
 * Meaning is carried by the TEXT label (and the full sentence for assistive technology); the
 * glyph, outline style (dotted / dashed / solid) and tone are redundant cues. A refusal, a gap,
 * an ambiguity and a not-requested value are never the same appearance, and none is an error:
 * only `ERROR` is an application failure.
 */
export function DomainState({ kind, reason, action, compact = false }: DomainStateProps) {
  const meta = STATE_META[kind];
  const description = reason ?? meta.sentence;
  const dashOnly = compact && (kind === "NOT_STATED" || kind === "NOT_APPLICABLE");
  const glyph: GlyphKind | null = kind === "NOT_APPLICABLE" ? null : (kind as GlyphKind);

  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5">
      <span
        data-state={kind}
        title={description}
        className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px] leading-4 font-medium whitespace-nowrap ${TONE_TEXT[meta.tone]} ${BORDER_STYLE[meta.border]} ${meta.border === "none" ? "" : TONE_OUTLINE[meta.tone]}`}
      >
        {glyph ? <StateGlyph kind={glyph} /> : null}
        {dashOnly ? (
          <>
            {kind === "NOT_APPLICABLE" ? <span aria-hidden="true">n/a</span> : null}
            {/* The state is still NAMED for assistive technology, just not shown in words. */}
            <span className="sr-only">{`${meta.label}: ${description}`}</span>
          </>
        ) : (
          <>
            <span>{meta.label}</span>
            <span className="sr-only">{`: ${description}`}</span>
          </>
        )}
      </span>
      {action ? <span className="text-xs">{action}</span> : null}
    </span>
  );
}
