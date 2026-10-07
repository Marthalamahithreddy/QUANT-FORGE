import { StateGlyph } from "@/components/state/glyphs";
import type { VerificationStatus } from "@/lib/api/schema";
import { VERIFICATION_LABEL, VERIFICATION_SENTENCE } from "@/lib/record-wording";

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

/**
 * The look of each verification status. Deliberately NOT the generic success / warning / error
 * scale:
 *
 * - MATCH (equals sign, solid, info): the rerun reproduced the baseline;
 * - EVIDENCE_INCOMPLETE (half circle, solid, warning): the same half-filled mark as a PARTIAL
 *   reproducibility level, because it is that statement; it is not a change and not a failure;
 * - MANIFEST_CHANGED (not-equals sign, DASHED, neutral): the inputs differ;
 * - RESULT_CHANGED (opposed arrows, solid, neutral): the output differs.
 *
 * The two "changed" statuses are differences, not failures, so neither uses the danger tone. The
 * text label, the glyph and the outline style each carry the meaning, so none depends on colour.
 */
const LOOK: Record<VerificationStatus, { readonly tone: string; readonly outline: string }> = {
  MATCH: { tone: "text-info", outline: "border-solid border-info/60" },
  EVIDENCE_INCOMPLETE: { tone: "text-warning", outline: "border-solid border-warning/60" },
  MANIFEST_CHANGED: { tone: "text-text", outline: "border-dashed border-text-2/70" },
  RESULT_CHANGED: { tone: "text-text", outline: "border-solid border-text-2/70" },
};

function Glyph({ status }: { readonly status: VerificationStatus }) {
  switch (status) {
    case "MATCH":
      return (
        <svg {...COMMON} data-glyph="equals">
          <path d="M2.5 4.5h7M2.5 7.5h7" />
        </svg>
      );
    case "EVIDENCE_INCOMPLETE":
      return <StateGlyph kind="PARTIAL" />;
    case "MANIFEST_CHANGED":
      return (
        <svg {...COMMON} data-glyph="not-equals">
          <path d="M2.5 4.5h7M2.5 7.5h7M7.5 2l-3 8" />
        </svg>
      );
    case "RESULT_CHANGED":
      return (
        <svg {...COMMON} data-glyph="opposed-arrows">
          <path d="M2 4h8M8 2l2 2-2 2M10 8H2M4 6 2 8l2 2" />
        </svg>
      );
  }
}

/** The verifier's status, as text with a glyph. The status is only ever the one the API returned. */
export function VerificationBadge({ status }: { readonly status: VerificationStatus }) {
  const look = LOOK[status];
  return (
    <span
      data-verification={status}
      title={VERIFICATION_SENTENCE[status]}
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px] leading-4 font-medium whitespace-nowrap ${look.tone} ${look.outline}`}
    >
      <Glyph status={status} />
      {VERIFICATION_LABEL[status]}
    </span>
  );
}
