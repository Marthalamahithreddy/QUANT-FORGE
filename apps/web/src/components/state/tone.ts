import type { StateBorder, StateTone } from "@/lib/state";

/** Text and outline colours per tone. Foreground tokens are measured at >= 4.5:1 on surfaces. */
export const TONE_TEXT: Record<StateTone, string> = {
  muted: "text-text-3",
  neutral: "text-text-2",
  warning: "text-warning",
  info: "text-info",
  danger: "text-danger",
};

export const TONE_OUTLINE: Record<StateTone, string> = {
  muted: "border-text-3/50",
  neutral: "border-text-2/60",
  warning: "border-warning/60",
  info: "border-info/60",
  danger: "border-danger/60",
};

/** Dotted, dashed and solid are different statements (not decoration); `none` is transparent. */
export const BORDER_STYLE: Record<StateBorder, string> = {
  none: "border-transparent",
  solid: "border-solid",
  dashed: "border-dashed",
  dotted: "border-dotted",
};
