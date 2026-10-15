import { PercentValue } from "@/components/financial/percent-value";
import { FinancialValue } from "@/components/financial/financial-value";
import { DomainState } from "@/components/state/domain-state";
import type { MetricId, MetricSection, PointSection } from "@/lib/api/schema";
import { CHANGE_LABEL, METRIC_KIND, inMillions } from "@/lib/companies-view";
import { formatGroupedDecimal } from "@/lib/format";
import { PERCENTAGE_STATE, POINT_STATE } from "@/lib/state-map";
import type { NonValueState } from "@/lib/state";

/** USD shown in millions by moving the decimal point of the exact string; the exact USD is kept. */
export function MoneyValue({ exact, signed = false }: { readonly exact: string; readonly signed?: boolean }) {
  const millions = inMillions(exact);
  if (millions === null) return <DomainState kind="ERROR" reason="The value is not a valid decimal string." />;
  const sign = signed && !millions.startsWith("-") && millions !== "0" ? "+" : "";
  return (
    <span className="num" data-exact={exact} title={`Exact: ${exact} USD`}>
      {sign}
      {formatGroupedDecimal(millions)}
      <span className="sr-only">{` USD millions (exact ${exact} USD)`}</span>
    </span>
  );
}

/** One figure of one metric: its value, or the precise reason there is none. */
export function Figure({ point, metric }: { readonly point: PointSection; readonly metric: MetricId }) {
  const value = point.fact?.value ?? null;
  if (point.status === "AVAILABLE" && value !== null) {
    return METRIC_KIND[metric] === "usd" ? <MoneyValue exact={value} /> : <FinancialValue value={value} kind="eps" />;
  }
  const state = POINT_STATE[point.status];
  return <DomainState kind={state === "VALUE" ? "ERROR" : state} reason={`${point.status}: ${point.reason}`} />;
}

/** The exact change and its ratio, or why there is none. */
export function ChangeFigure({ section }: { readonly section: MetricSection }) {
  const { change, metric } = section;
  if (change.status !== "AVAILABLE" || change.absolute === null) {
    return <DomainState kind="UNAVAILABLE" reason="A change needs both the current and the prior-year figure." />;
  }
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2">
      {METRIC_KIND[metric] === "usd" ? <MoneyValue exact={change.absolute} signed /> : <FinancialValue value={change.absolute} kind="signed" />}
      {change.ratio !== null ? (
        <span className="type-meta">
          (<PercentValue ratio={change.ratio} />)
        </span>
      ) : change.ratio_unavailable_reason !== null ? (
        <DomainState
          kind={PERCENTAGE_STATE[change.ratio_unavailable_reason] as NonValueState}
          reason={`No percentage change: the prior-year figure is ${change.ratio_unavailable_reason === "ZERO_COMPARATIVE" ? "zero" : "negative"}.`}
        />
      ) : null}
      {change.transition !== null ? <span className="type-meta">{CHANGE_LABEL[change.transition]}</span> : null}
    </span>
  );
}
