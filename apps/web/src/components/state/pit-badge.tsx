import { StatusBadge } from "@/components/state/status-badge";
import type { PitEligibility } from "@/lib/api/schema";
import { PIT_PRESENTATION } from "@/lib/availability";
import { PIT_STATE } from "@/lib/state-map";

/**
 * The backend's point-in-time classification of one stored event, as ONE badge: the same label,
 * glyph, dashed outline and tone on every surface that shows it (Earnings list and detail, the
 * Research finder, Overview). Nothing is derived here; `NOT_APPLICABLE` is not a point-in-time
 * state and so carries no point-in-time hue.
 */
export function PitBadge({ eligibility }: { readonly eligibility: PitEligibility }) {
  const state = PIT_STATE[eligibility];
  return (
    <StatusBadge kind={state} mode={state === "NOT_APPLICABLE" ? undefined : "pit"}>
      {PIT_PRESENTATION[eligibility].label}
    </StatusBadge>
  );
}
