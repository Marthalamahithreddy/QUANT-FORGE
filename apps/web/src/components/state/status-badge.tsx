import type { ReactNode } from "react";

import { StateGlyph, type GlyphKind } from "@/components/state/glyphs";
import { BORDER_STYLE, TONE_OUTLINE, TONE_TEXT } from "@/components/state/tone";
import { STATE_META, type StateKind } from "@/lib/state";

export type ModeHue = "descriptive" | "pit";

export interface StatusBadgeProps {
  /** A domain state, or `VALUE` for a section that is available. */
  readonly kind: StateKind;
  /**
   * The research mode this badge labels. Descriptive uses a solid outline and the descriptive
   * hue; point-in-time uses a DASHED outline and the point-in-time hue (so the two stay distinct
   * in greyscale). Omit for an ordinary state badge.
   */
  readonly mode?: ModeHue;
  /** The status text shown (for example `AVAILABLE`, `REFUSED`, `EVIDENCE_ELIGIBLE`). */
  readonly children: ReactNode;
}

/** The hue of a mode, and the outline a VALUE badge of that mode uses. */
const MODE_HUE: Record<ModeHue, { text: string; outline: string; valueBorder: string }> = {
  descriptive: { text: "text-mode-descriptive", outline: "border-mode-descriptive/60", valueBorder: "border-solid" },
  pit: { text: "text-mode-pit", outline: "border-mode-pit/70", valueBorder: "border-dashed" },
};

/**
 * A compact status chip for a section header. The text states the status; the glyph and outline
 * repeat it. It replaces the old four-tone `Badge`, whose green/amber/red dots mixed status with
 * evaluation.
 */
export function StatusBadge({ kind, mode, children }: StatusBadgeProps) {
  const meta = kind === "VALUE" ? null : STATE_META[kind];
  const glyph: GlyphKind | null =
    kind === "VALUE" || kind === "NOT_APPLICABLE" ? null : (kind as GlyphKind);
  // A mode gives the badge its HUE; a non-value state keeps its own outline style (dotted, dashed,
  // solid), so a refused descriptive badge never looks like an available one. The glyph and the
  // status text repeat the state either way.
  const palette = mode
    ? `${MODE_HUE[mode].text} ${MODE_HUE[mode].outline} ${meta ? BORDER_STYLE[meta.border] : MODE_HUE[mode].valueBorder}`
    : meta
      ? `${BORDER_STYLE[meta.border]} ${TONE_OUTLINE[meta.tone]} ${TONE_TEXT[meta.tone]}`
      : "border-solid border-border text-text";
  return (
    <span
      data-state={kind}
      data-mode={mode}
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px] leading-4 font-medium whitespace-nowrap ${palette}`}
    >
      {glyph ? <StateGlyph kind={glyph} /> : null}
      {children}
    </span>
  );
}
