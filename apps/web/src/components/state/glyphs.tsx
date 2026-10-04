import type { StateKind } from "@/lib/state";

/**
 * The state glyphs: 12px inline SVG, stroke-only, authored here (no icon library).
 * Decorative: always paired with a text label, never the only cue.
 */
export type GlyphKind = Exclude<StateKind, "VALUE" | "NOT_APPLICABLE"> | "COMPLETE";

const COMMON = {
  width: 12,
  height: 12,
  viewBox: "0 0 12 12",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  focusable: false,
  className: "shrink-0",
} as const;

export function StateGlyph({ kind }: { readonly kind: GlyphKind }) {
  switch (kind) {
    case "NOT_STATED":
      // an en dash
      return (
        <svg {...COMMON} data-glyph="dash">
          <path d="M2.5 6h7" />
        </svg>
      );
    case "NOT_REQUESTED":
      // a dotted ring
      return (
        <svg {...COMMON} data-glyph="dotted-ring">
          <circle cx="6" cy="6" r="4.25" strokeDasharray="0.1 2.6" />
        </svg>
      );
    case "MISSING":
      // a hollow circle
      return (
        <svg {...COMMON} data-glyph="ring">
          <circle cx="6" cy="6" r="4.25" />
        </svg>
      );
    case "UNAVAILABLE":
      // an open diamond
      return (
        <svg {...COMMON} data-glyph="diamond">
          <path d="M6 1.5 10.5 6 6 10.5 1.5 6Z" />
        </svg>
      );
    case "REFUSED":
      // a circle with a slash
      return (
        <svg {...COMMON} data-glyph="circle-slash">
          <circle cx="6" cy="6" r="4.25" />
          <path d="M3 9 9 3" />
        </svg>
      );
    case "AMBIGUOUS":
      // a fork: one input, two candidates
      return (
        <svg {...COMMON} data-glyph="branch">
          <path d="M6 10.5V6M6 6 3 1.5M6 6l3-4.5" />
        </svg>
      );
    case "PARTIAL":
      // a half-filled circle
      return (
        <svg {...COMMON} data-glyph="half-circle">
          <circle cx="6" cy="6" r="4.25" />
          <path d="M6 1.75a4.25 4.25 0 0 1 0 8.5Z" fill="currentColor" />
        </svg>
      );
    case "COMPLETE":
      // a filled ring
      return (
        <svg {...COMMON} data-glyph="filled-ring">
          <circle cx="6" cy="6" r="4.25" />
          <circle cx="6" cy="6" r="2" fill="currentColor" />
        </svg>
      );
    case "ERROR":
      // a warning triangle
      return (
        <svg {...COMMON} data-glyph="triangle">
          <path d="M6 1.5 11 10.5H1Z" />
          <path d="M6 5v2.5M6 9v.01" />
        </svg>
      );
  }
}
