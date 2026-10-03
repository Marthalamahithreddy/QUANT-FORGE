import type { ReactNode } from "react";

import type { Tone } from "@/lib/availability";

const TONE_CLASS: Record<Tone, { box: string; dot: string }> = {
  neutral: { box: "border-border-strong text-text-secondary", dot: "bg-text-muted" },
  positive: { box: "border-status-ok/40 text-text-primary", dot: "bg-status-ok" },
  caution: { box: "border-status-warn/40 text-text-primary", dot: "bg-status-warn" },
  negative: { box: "border-status-down/40 text-text-primary", dot: "bg-status-down" },
};

export interface BadgeProps {
  readonly tone?: Tone;
  readonly children: ReactNode;
}

/**
 * The single status chip used across the workspace.
 *
 * Four tones only. The tone adds a coloured dot; the meaning is always carried
 * by the text, so it never depends on colour alone.
 */
export function Badge({ tone = "neutral", children }: BadgeProps) {
  const { box, dot } = TONE_CLASS[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px] leading-4 font-medium whitespace-nowrap ${box}`}
    >
      <span aria-hidden="true" className={`size-1.5 rounded-full ${dot}`} />
      {children}
    </span>
  );
}
