import { DomainState } from "@/components/state/domain-state";
import { PercentValue } from "@/components/financial/percent-value";
import {
  MISSING,
  directionOf,
  formatDecimal,
  formatGroupedDecimal,
  formatSignedDecimal,
  isExactDecimal,
} from "@/lib/format";
import type { NonValueState } from "@/lib/state";

/**
 * What the value is, which decides how it is shown (spec section 10.2):
 *
 * - `price`, `eps`: the exact string as sent, scale preserved;
 * - `money`: exact digits, integer part grouped (lossless);
 * - `signed`: exact string with a `+` on non-zero positives (surprise, EPS change);
 * - `ratio`: a ratio shown as a percent (`0.125` -> `12.50%`), the exact ratio kept available.
 */
export type FinancialKind = "price" | "eps" | "money" | "signed" | "ratio";

export interface FinancialValueProps {
  /** The exact string from the API, or `null` when the source stated none. Never a `number`. */
  readonly value: string | null;
  readonly kind: FinancialKind;
  /**
   * A non-value state decided by the caller from a backend status (missing, refused, ...). When
   * given it replaces the value; nothing is shown as `0` or blank in its place.
   */
  readonly state?: NonValueState;
  /** The backend's reason for `state`, verbatim. */
  readonly reason?: string;
}

/**
 * The ONLY way a financial string is rendered. Server-compatible and string-only: the value is
 * never converted to a JS number for display, so no digit is lost to binary floating point.
 *
 * `null` is NOT_STATED (the source gave nothing), never zero. A non-null string that is not a
 * plain decimal is a contract failure and is shown as an ERROR rather than repaired.
 */
export function FinancialValue({ value, kind, state, reason }: FinancialValueProps) {
  if (state !== undefined) {
    return <DomainState kind={state} reason={reason} />;
  }
  if (value === null) {
    return <DomainState kind="NOT_STATED" reason={reason} compact />;
  }
  if (!isExactDecimal(value)) {
    return <DomainState kind="ERROR" reason="The value is not a valid decimal string." />;
  }
  if (kind === "ratio") {
    return <PercentValue ratio={value} />;
  }

  const text =
    kind === "money"
      ? formatGroupedDecimal(value)
      : kind === "signed"
        ? formatSignedDecimal(value)
        : formatDecimal(value);
  const direction = kind === "signed" ? directionOf(value) : null;

  return (
    <span className="num" data-exact={value} data-direction={direction ?? undefined}>
      {text === MISSING ? value : text}
    </span>
  );
}
