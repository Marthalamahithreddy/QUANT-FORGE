import { FinancialValue } from "@/components/financial/financial-value";
import { PercentValue } from "@/components/financial/percent-value";
import { DomainState } from "@/components/state/domain-state";
import type { EpsSurpriseStatus } from "@/lib/api/schema";
import type { NonValueState, StateKind } from "@/lib/state";
import { SURPRISE_STATE } from "@/lib/state-map";

export interface EpsSurpriseValueProps {
  readonly status: EpsSurpriseStatus;
  /** Exact strings from the API; the surprise is the backend's, never recomputed here. */
  readonly absolute: string | null;
  readonly ratio: string | null;
  readonly reason: string;
}

/** A surprise with no figure is not a VALUE by definition; an AVAILABLE one without a figure is shown as not stated. */
const withoutFigure = (kind: StateKind): NonValueState => (kind === "VALUE" ? "NOT_STATED" : kind);

/**
 * The EPS surprise as the backend states it: the signed exact absolute figure with its ratio as a
 * percent, or the state of why there is none (with the backend's reason), plus the status code.
 */
export function EpsSurpriseValue({ status, absolute, ratio, reason }: EpsSurpriseValueProps) {
  return (
    <div className="flex flex-col gap-1">
      {absolute === null ? (
        <DomainState kind={withoutFigure(SURPRISE_STATE[status])} reason={reason} />
      ) : (
        <span className="flex flex-wrap items-baseline gap-x-3">
          <FinancialValue kind="signed" value={absolute} />
          {ratio === null ? (
            <DomainState kind="UNAVAILABLE" reason={reason} />
          ) : (
            <span className="text-text-2">
              <PercentValue ratio={ratio} />
            </span>
          )}
        </span>
      )}
      <span className="type-meta">
        <span className="type-id">{status}</span> · {reason}
      </span>
    </div>
  );
}
