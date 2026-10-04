import { DomainState } from "@/components/state/domain-state";
import {
  directionOf,
  formatRatioAsPercent,
  isExactDecimal,
  ratioRoundsToZero,
} from "@/lib/format";

export interface PercentValueProps {
  /** A RATIO string (`0.125` means 12.5%), exactly as the API sent it. */
  readonly ratio: string;
  /** Displayed decimals; rounding is half away from zero on the digits, never via a float. */
  readonly places?: number;
}

/**
 * A ratio shown as a percent by shifting the decimal point exactly. The exact ratio stays
 * available (hover hint and assistive text), and a non-zero ratio that rounds to `0.00%` says so
 * instead of looking like an exact zero. The component, not the call site, supplies the `%`.
 */
export function PercentValue({ ratio, places = 2 }: PercentValueProps) {
  if (!isExactDecimal(ratio)) {
    return <DomainState kind="ERROR" reason="The ratio is not a valid decimal string." />;
  }
  const text = formatRatioAsPercent(ratio, places);
  const hidden = ratioRoundsToZero(ratio, places);
  return (
    <span className="num" data-exact={ratio} data-direction={directionOf(ratio) ?? undefined} title={`Exact ratio: ${ratio}`}>
      {text}
      <span className="sr-only">{` (exact ratio ${ratio})`}</span>
      {hidden ? <span className="type-meta">{" (not zero)"}</span> : null}
    </span>
  );
}
