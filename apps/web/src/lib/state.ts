/**
 * The domain-state vocabulary (design spec section 4).
 *
 * These ten states are materially different statements and must never share one appearance:
 * missing, unknown, not requested, not applicable, refused, partial, ambiguous, unavailable, or an
 * application error. Meaning is carried by TEXT (label and sentence); the glyph, border style and
 * tone are redundant cues, so nothing depends on colour alone.
 *
 * Only the vocabulary lives here. The mapping from each backend status to a state is added with the
 * pages that display those statuses (11.1C onward); nothing is invented ahead of the contract.
 */

export const STATE_KINDS = [
  "VALUE",
  "NOT_STATED",
  "NOT_REQUESTED",
  "NOT_APPLICABLE",
  "MISSING",
  "UNAVAILABLE",
  "REFUSED",
  "AMBIGUOUS",
  "PARTIAL",
  "ERROR",
] as const;
export type StateKind = (typeof STATE_KINDS)[number];

/** A state other than VALUE: it always has a label. */
export type NonValueState = Exclude<StateKind, "VALUE">;

export type StateBorder = "none" | "dotted" | "solid" | "dashed";
export type StateTone = "muted" | "neutral" | "warning" | "info" | "danger";

export interface StateMeta {
  /** Short label shown next to the glyph. */
  readonly label: string;
  /** One sentence explaining the state (shown on focus and to assistive technology). */
  readonly sentence: string;
  /** Outline style: dotted, dashed and solid are different statements, not decoration. */
  readonly border: StateBorder;
  readonly tone: StateTone;
}

export const STATE_META: Record<NonValueState, StateMeta> = {
  NOT_STATED: {
    label: "Not stated",
    sentence: "The source did not supply this.",
    border: "none",
    tone: "muted",
  },
  NOT_REQUESTED: {
    label: "Not requested",
    sentence: "You did not ask for this.",
    border: "dotted",
    tone: "neutral",
  },
  NOT_APPLICABLE: {
    label: "Not applicable",
    sentence: "This does not apply here.",
    border: "none",
    tone: "muted",
  },
  MISSING: {
    label: "Missing",
    sentence: "Stored data needed for this is absent.",
    border: "solid",
    tone: "warning",
  },
  UNAVAILABLE: {
    label: "Unavailable",
    sentence: "This cannot be derived because an input was unavailable.",
    border: "solid",
    tone: "neutral",
  },
  REFUSED: {
    label: "Refused",
    sentence: "The engine declined to compute this by design.",
    border: "dashed",
    tone: "neutral",
  },
  AMBIGUOUS: {
    label: "Ambiguous",
    sentence: "More than one candidate exists; the engine did not choose.",
    border: "solid",
    tone: "info",
  },
  PARTIAL: {
    label: "Partial",
    sentence: "Some parts are available and some are not.",
    border: "solid",
    tone: "warning",
  },
  ERROR: {
    label: "Error",
    sentence: "The application could not complete this request.",
    border: "solid",
    tone: "danger",
  },
};

export const REPRODUCIBILITY_LEVELS = ["COMPLETE", "PARTIAL"] as const;
export type ReproducibilityLevel = (typeof REPRODUCIBILITY_LEVELS)[number];

export const REPRODUCIBILITY_META: Record<
  ReproducibilityLevel,
  { readonly label: string; readonly sentence: string }
> = {
  COMPLETE: {
    label: "Complete",
    sentence:
      "Every consumed piece of evidence is pinned to a recorded ingestion run. Residual assumptions still apply.",
  },
  PARTIAL: {
    label: "Partial",
    sentence:
      "Some consumed evidence has no complete immutable provenance pin. The numbers are not wrong; the evidence trail is incomplete.",
  },
};
