import { StatusBadge } from "@/components/state/status-badge";
import type { Observation } from "@/lib/api/schema";
import { ACCOUNTING_STATE_LABEL, POINT_IN_TIME_STATE_LABEL, SECTION_STATE_LABEL } from "@/lib/research-wording";
import { ACCOUNTING_STATE, POINT_IN_TIME_STATE, SECTION_STATE } from "@/lib/state-map";

const SEGMENT = "flex flex-col gap-1 rounded border border-border-subtle bg-surface px-3 py-2 hover:bg-hover max-lg:min-h-11";

/**
 * Four compact segments, each an in-page link: the descriptive state, the point-in-time state, the
 * accounting state and the refusal count. A point-in-time refusal is therefore visible without
 * scrolling and never reads as the whole result.
 */
export function ResultStatusStrip({ observation }: { readonly observation: Observation }) {
  const { descriptive, point_in_time: pit, accounting, refusals } = observation;
  return (
    <nav aria-label="Result status" className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <a href="#section-descriptive" className={SEGMENT}>
        <span className="type-label">Descriptive</span>
        <StatusBadge kind={SECTION_STATE[descriptive.state]} mode="descriptive">
          {SECTION_STATE_LABEL[descriptive.state]}
        </StatusBadge>
      </a>
      <a href="#section-point-in-time" className={SEGMENT}>
        <span className="type-label">Point-in-time</span>
        <StatusBadge kind={POINT_IN_TIME_STATE[pit.state]} mode="pit">
          {POINT_IN_TIME_STATE_LABEL[pit.state]}
        </StatusBadge>
      </a>
      <a href="#section-accounting" className={SEGMENT}>
        <span className="type-label">Accounting</span>
        <StatusBadge kind={ACCOUNTING_STATE[accounting.state]}>{ACCOUNTING_STATE_LABEL[accounting.state]}</StatusBadge>
      </a>
      <a href="#refusal-ledger" className={SEGMENT}>
        <span className="type-label">Refusals</span>
        <span className="num text-sm text-text" data-testid="refusal-count">
          {refusals.length}
        </span>
      </a>
    </nav>
  );
}
