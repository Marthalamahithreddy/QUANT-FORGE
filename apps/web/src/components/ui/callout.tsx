import type { ReactNode } from "react";

import type { StateBorder, StateTone } from "@/lib/state";

const RULE_COLOUR: Record<StateTone, string> = {
  muted: "border-l-text-3",
  neutral: "border-l-text-2",
  warning: "border-l-warning",
  info: "border-l-info",
  danger: "border-l-danger",
};

const RULE_STYLE: Record<StateBorder, string> = {
  none: "border-solid",
  solid: "border-solid",
  dashed: "border-dashed",
  dotted: "border-dotted",
};

export interface CalloutProps {
  readonly tone?: StateTone;
  /** Solid, dashed and dotted rules are different statements (see the state system). */
  readonly rule?: StateBorder;
  readonly role?: "note" | "status" | "alert";
  readonly label?: string;
  readonly children: ReactNode;
}

/**
 * A boundary note, a refusal or a legend: a left rule and text, no fill and no box.
 * Colour is never the only cue; the content states what it is.
 */
export function Callout({ tone = "neutral", rule = "solid", role = "note", label, children }: CalloutProps) {
  return (
    <div
      role={role}
      aria-label={label}
      className={`border-l-[3px] py-1.5 pl-3 text-sm text-text ${RULE_COLOUR[tone]} ${RULE_STYLE[rule]}`}
    >
      {children}
    </div>
  );
}
