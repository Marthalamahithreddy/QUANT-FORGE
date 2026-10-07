import { ComparativeEpsPanel, CurrentEpsPanel, EpsChangePanel } from "@/components/filings/eps-panels";
import { FilingLinkFrame } from "@/components/research/accounting-section";
import { RefusalNotice } from "@/components/state/refusal-notice";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import type { StoredResult } from "@/lib/api/record-types";
import { EPS_KIND_LABEL, PERIOD_CLASS_LABEL } from "@/lib/filings-view";
import { ACCOUNTING_STATE_LABEL } from "@/lib/research-wording";
import { storedEpsPanels } from "@/lib/stored-result";

/**
 * The stored filing link, disclosure evidence and accounting. The link frame is the same component
 * the live workspace uses (so the link states, PERIOD_UNVERIFIED and the D0/D1 wording read
 * identically); the EPS panels are the filing pages' own, fed from the stored change through
 * `storedEpsPanels`. Every figure is the stored string. No filing is inferred, and there is no
 * "supply a filing" link: a stored request cannot be edited.
 */
export function StoredAccounting({ result }: { readonly result: StoredResult }) {
  const { accounting, filing_link: link, disclosure_evidence: disclosure } = result;
  return (
    <div className="flex flex-col gap-4">
      <FilingLinkFrame link={link} disclosure={disclosure} />
      {accounting.state === "NOT_REQUESTED" ? (
        <RefusalNotice kind="NOT_REQUESTED" title="Accounting" reason="No filing was supplied for this request, so no accounting was evaluated." />
      ) : accounting.state === "LINK_REFUSED" ? (
        <RefusalNotice kind="REFUSED" title="Accounting" reason={`The filing link was refused (${link.status}), so the accounting was not evaluated.`}>
          <span className="type-id text-text-3">{accounting.state}</span> · {ACCOUNTING_STATE_LABEL[accounting.state]}
        </RefusalNotice>
      ) : accounting.change !== null ? (
        <StoredEps change={accounting.change} state={accounting.state} />
      ) : null}
    </div>
  );
}

function StoredEps({ change, state }: { readonly change: NonNullable<StoredResult["accounting"]["change"]>; readonly state: string }) {
  const panels = storedEpsPanels(change);
  const request = change.comparative;
  return (
    <>
      <p className="type-meta">
        Accounting: <span className="type-id">{state}</span>. Descriptive accounting data from the supplied filing, as stored; it carries no point-in-time claim.
      </p>
      <KeyValueList>
        <KeyValue label="Accession">
          <span className="type-id">{request.accession_number}</span>
        </KeyValue>
        {request.form ? <KeyValue label="Form">{request.form}</KeyValue> : null}
        <KeyValue label="Period class">{PERIOD_CLASS_LABEL[request.period_class]}</KeyValue>
        <KeyValue label="EPS kind">{EPS_KIND_LABEL[request.eps_kind]}</KeyValue>
      </KeyValueList>
      <CurrentEpsPanel current={panels.current} />
      <ComparativeEpsPanel comparative={panels.comparative} />
      <EpsChangePanel change={panels.change} />
    </>
  );
}
