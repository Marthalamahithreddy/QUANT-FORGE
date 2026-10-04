import { StateGlyph } from "@/components/state/glyphs";
import { REPRODUCIBILITY_META, type ReproducibilityLevel } from "@/lib/state";

export interface ReproducibilityBadgeProps {
  /** The level the DOMAIN supplied (a saved baseline). Never invented for an unsaved study. */
  readonly level: ReproducibilityLevel;
}

/**
 * COMPLETE or PARTIAL, as text with a glyph.
 *
 * PARTIAL means some consumed evidence lacks a complete immutable provenance pin. It is a
 * statement about the evidence trail, not about correctness: it is styled as caution, never as a
 * failure, and its sentence says so. The one-line meaning is in the accessible description.
 */
export function ReproducibilityBadge({ level }: ReproducibilityBadgeProps) {
  const meta = REPRODUCIBILITY_META[level];
  const partial = level === "PARTIAL";
  return (
    <span
      data-reproducibility={level}
      title={meta.sentence}
      className={`inline-flex items-center gap-1.5 rounded border border-solid px-1.5 py-0.5 text-[11px] leading-4 font-medium whitespace-nowrap ${
        partial ? "border-warning/60 text-warning" : "border-info/60 text-info"
      }`}
    >
      <StateGlyph kind={partial ? "PARTIAL" : "COMPLETE"} />
      {meta.label}
      <span className="sr-only">{`: ${meta.sentence}`}</span>
    </span>
  );
}
