import type { ReactNode } from "react";

import { FinancialValue } from "@/components/financial/financial-value";
import { PercentValue } from "@/components/financial/percent-value";
import { DomainState } from "@/components/state/domain-state";
import { StatusBadge } from "@/components/state/status-badge";
import { Frame } from "@/components/ui/frame";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import type {
  ComparativeEps,
  CurrentEps,
  EpsChange,
  FactEvidence,
  FilingEps,
} from "@/lib/api/schema";
import {
  CHANGE_STATUS_LABEL,
  COMPARATIVE_STATUS_LABEL,
  EPS_KIND_LABEL,
  EPS_STATUS_LABEL,
  PERCENTAGE_REASON_DETAIL,
  PERIOD_CLASS_LABEL,
  TRANSITION_LABEL,
} from "@/lib/filings-view";
import { formatDate } from "@/lib/format";
import type { NonValueState, StateKind } from "@/lib/state";
import { CHANGE_STATE, COMPARATIVE_STATE, EPS_STATE } from "@/lib/state-map";

const code = (text: string) => <span className="type-id text-text-3">{text}</span>;

/**
 * The backend's status, said three ways that never replace each other: the domain state (its
 * mark), the status's own label, and its code. EXTENSION_ONLY, WRONG_UNIT, DIMENSIONAL_ONLY,
 * COREG_ONLY, MISSING and the rest are therefore always distinguishable, even though several
 * share the REFUSED mark.
 */
function StatusRows({
  state,
  label,
  status,
  reason,
}: {
  readonly state: StateKind;
  readonly label: string;
  readonly status: string;
  readonly reason: string;
}) {
  return (
    <>
      <KeyValue label="Status">
        <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
          {state === "VALUE" ? <span>{label}</span> : <DomainState kind={state as NonValueState} reason={reason} />}
          {state === "VALUE" ? null : <span className="text-text">{label}</span>}
          {code(status)}
        </span>
      </KeyValue>
      <KeyValue label="Reason">
        <span className="text-text-2">{reason}</span>
      </KeyValue>
    </>
  );
}

/** A fact value: the exact string, or an explicit statement that the source gave none (never 0). */
function FactRows({ fact, kind }: { readonly fact: FactEvidence; readonly kind: "eps" }) {
  return (
    <>
      <KeyValue label="Value">
        {fact.value === null ? (
          <DomainState kind="NOT_STATED" reason="The source stated no value (null, not zero)." />
        ) : (
          <FinancialValue kind={kind} value={fact.value} />
        )}
      </KeyValue>
      <KeyValue label="Unit">{fact.unit}</KeyValue>
      <KeyValue label="Period end">{formatDate(fact.period_end)}</KeyValue>
    </>
  );
}

function PanelBadge({ state, children }: { readonly state: StateKind; readonly children: ReactNode }) {
  return <StatusBadge kind={state}>{children}</StatusBadge>;
}

export function CurrentEpsPanel({ current }: { readonly current: CurrentEps }) {
  const state = EPS_STATE[current.status];
  return (
    <Frame title="Current EPS" actions={<PanelBadge state={state}>{EPS_STATUS_LABEL[current.status]}</PanelBadge>}>
      <KeyValueList>
        <StatusRows state={state} label={EPS_STATUS_LABEL[current.status]} status={current.status} reason={current.reason} />
        {current.fact ? <FactRows fact={current.fact} kind="eps" /> : null}
      </KeyValueList>
    </Frame>
  );
}

export function ComparativeEpsPanel({ comparative }: { readonly comparative: ComparativeEps }) {
  const state = COMPARATIVE_STATE[comparative.status];
  return (
    <Frame
      title="Comparative EPS"
      note="As presented in this filing"
      actions={<PanelBadge state={state}>{COMPARATIVE_STATUS_LABEL[comparative.status]}</PanelBadge>}
    >
      <KeyValueList>
        <StatusRows
          state={state}
          label={COMPARATIVE_STATUS_LABEL[comparative.status]}
          status={comparative.status}
          reason={comparative.reason}
        />
        {comparative.fact ? <FactRows fact={comparative.fact} kind="eps" /> : null}
        {comparative.status === "COMPARATIVE_NULL_VALUE" ? (
          <KeyValue label="Source evidence">
            Matching source evidence exists, but its value is null. It is not treated as zero.
          </KeyValue>
        ) : null}
      </KeyValueList>
      <p className="type-meta mt-3 max-w-3xl">
        This is the prior-period figure shown in the same filing&rsquo;s own comparative column, not the earlier
        filing&rsquo;s original number and not a restatement search.
      </p>
    </Frame>
  );
}

export function EpsChangePanel({ change }: { readonly change: EpsChange }) {
  const state = CHANGE_STATE[change.status];
  return (
    <Frame
      title="EPS change"
      note="Current minus comparative, computed by the API"
      actions={<PanelBadge state={state}>{CHANGE_STATUS_LABEL[change.status]}</PanelBadge>}
    >
      <KeyValueList>
        <KeyValue label="Status">
          <span className="inline-flex flex-wrap items-center gap-x-2">
            {state === "VALUE" ? null : <DomainState kind={state as NonValueState} />}
            <span>{CHANGE_STATUS_LABEL[change.status]}</span>
            {code(change.status)}
          </span>
        </KeyValue>
        {change.status === "INPUT_UNAVAILABLE" || change.eps_change === null ? (
          <KeyValue label="EPS change">
            <span className="text-text-2">
              Not computed: the current or comparative EPS is unavailable (see their statuses above). No value is shown.
            </span>
          </KeyValue>
        ) : (
          <>
            <KeyValue label="EPS change">
              <FinancialValue kind="signed" value={change.eps_change} />
            </KeyValue>
            <KeyValue label="Transition">
              {change.transition === null ? (
                <DomainState kind="NOT_STATED" compact />
              ) : (
                <span className="inline-flex flex-wrap items-center gap-x-2">
                  {TRANSITION_LABEL[change.transition]} {code(change.transition)}
                </span>
              )}
            </KeyValue>
            <KeyValue label="Percentage change">
              {change.percentage_change === null ? (
                <span className="flex flex-col gap-1">
                  <DomainState
                    kind="UNAVAILABLE"
                    reason={
                      change.percentage_unavailable_reason === null
                        ? "Percentage change is not defined for this comparative base."
                        : PERCENTAGE_REASON_DETAIL[change.percentage_unavailable_reason]
                    }
                  />
                  <span className="type-meta">
                    Percentage change is not defined for this comparative base
                    {change.percentage_unavailable_reason === null
                      ? "."
                      : `. ${PERCENTAGE_REASON_DETAIL[change.percentage_unavailable_reason]}`}
                  </span>
                </span>
              ) : (
                <PercentValue ratio={change.percentage_change} />
              )}
            </KeyValue>
          </>
        )}
      </KeyValueList>
    </Frame>
  );
}

function FactEvidenceBlock({ title, fact }: { readonly title: string; readonly fact: FactEvidence }) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-xs font-medium text-text-2">{title}</h3>
      <KeyValueList>
        <KeyValue label="Fact id">{code(fact.fact_id)}</KeyValue>
        <KeyValue label="Tag">{code(fact.tag)}</KeyValue>
        <KeyValue label="Taxonomy version">{code(fact.taxonomy_version)}</KeyValue>
        <KeyValue label="Unit">{fact.unit}</KeyValue>
        <KeyValue label="Period end">{formatDate(fact.period_end)}</KeyValue>
        <KeyValue label="Quarters (qtrs)">{fact.qtrs}</KeyValue>
        <KeyValue label="Segments">
          {fact.segments === "" ? <span className="text-text-3">(empty, as stored)</span> : <code>{fact.segments}</code>}
        </KeyValue>
        <KeyValue label="Co-registrant">
          {fact.coreg === "" ? <span className="text-text-3">(empty, as stored)</span> : <code>{fact.coreg}</code>}
        </KeyValue>
        <KeyValue label="Exact value">
          {fact.value === null ? <span className="text-text-3">null</span> : <code>{fact.value}</code>}
        </KeyValue>
      </KeyValueList>
    </div>
  );
}

/** Selected facts and exact strings as the API sent them; no availability metadata is invented. */
export function EvidenceDetails({ eps }: { readonly eps: FilingEps }) {
  const { current, comparative, change } = eps;
  return (
    <details className="rounded border border-border bg-surface">
      <summary className="cursor-pointer rounded px-3 py-2 text-sm font-medium text-text max-lg:min-h-11">
        Evidence and exact values
      </summary>
      <div className="flex flex-col gap-5 border-t border-border-subtle p-3">
        <p className="type-meta">
          Exactly what the API returned for the selected facts. It carries no availability or acceptance time.
        </p>
        {current.fact ? <FactEvidenceBlock title="Current EPS fact" fact={current.fact} /> : null}
        {comparative.fact ? <FactEvidenceBlock title="Comparative EPS fact" fact={comparative.fact} /> : null}
        {change.eps_change !== null ? (
          <KeyValueList>
            <KeyValue label="Exact EPS change">
              <code>{change.eps_change}</code>
            </KeyValue>
            <KeyValue label="Exact ratio">
              {change.percentage_change === null ? (
                <span className="text-text-3">not defined</span>
              ) : (
                <code>{change.percentage_change}</code>
              )}
            </KeyValue>
          </KeyValueList>
        ) : null}
        {current.fact === null && comparative.fact === null && change.eps_change === null ? (
          <p className="text-sm text-text-2">No fact was selected for this request.</p>
        ) : null}
      </div>
    </details>
  );
}

export function RequestSummary({ eps }: { readonly eps: FilingEps }) {
  return (
    <p className="type-meta">
      Showing {PERIOD_CLASS_LABEL[eps.period_class].toLowerCase()} EPS · {EPS_KIND_LABEL[eps.eps_kind].toLowerCase()}. No
      fallback to another EPS concept is made.
    </p>
  );
}
