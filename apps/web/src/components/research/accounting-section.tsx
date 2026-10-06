import { CurrentEpsPanel, ComparativeEpsPanel, EpsChangePanel, RequestSummary } from "@/components/filings/eps-panels";
import { RefusalReference } from "@/components/research/refusal-ledger";
import { DomainState } from "@/components/state/domain-state";
import { RefusalNotice } from "@/components/state/refusal-notice";
import { StatusBadge } from "@/components/state/status-badge";
import { Callout } from "@/components/ui/callout";
import { Frame } from "@/components/ui/frame";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import type { Observation } from "@/lib/api/schema";
import type { NonValueState } from "@/lib/state";
import { ACCOUNTING_STATE_LABEL, DISCLOSURE_CAVEAT, DISCLOSURE_WORDING, FILING_LINK_LABEL, PERIOD_VERIFICATION_NOTE } from "@/lib/research-wording";
import { FILING_LINK_STATE } from "@/lib/state-map";

/**
 * The filing link, its disclosure evidence and the accounting it enabled.
 *
 * A filing exists here ONLY because the reader supplied its accession (and both EPS selectors):
 * nothing is inferred, nearest, latest or matched by date or CIK. The link states are kept
 * distinct (not supplied, not stored, registrant mismatch, consistent), `PERIOD_UNVERIFIED` is
 * shown as exactly that, and the disclosure level is a category of stored metadata with no time.
 */
export function AccountingSection({ observation, configHref }: { readonly observation: Observation; readonly configHref: string }) {
  const { filing_link: link, disclosure_evidence: disclosure, accounting, refusals } = observation;
  const linkState = FILING_LINK_STATE[link.status];
  const verification = PERIOD_VERIFICATION_NOTE[link.period_verification];

  return (
    <div className="flex flex-col gap-4">
      <Frame
        title="Filing link and disclosure evidence"
        actions={<StatusBadge kind={linkState}>{FILING_LINK_LABEL[link.status]}</StatusBadge>}
      >
        <KeyValueList>
          <KeyValue label="Filing link">
            <span className="inline-flex flex-wrap items-center gap-x-2">
              {linkState === "VALUE" ? null : <DomainState kind={linkState as NonValueState} reason={link.reason} />}
              <span>{FILING_LINK_LABEL[link.status]}</span>
              <span className="type-id text-text-3">{link.status}</span>
            </span>
          </KeyValue>
          <KeyValue label="Reason">
            <span className="text-text-2">{link.reason}</span>
          </KeyValue>
          {link.accession_number ? (
            <KeyValue label="Accession">
              <span className="type-id">{link.accession_number}</span>
            </KeyValue>
          ) : null}
          {link.form ? <KeyValue label="Form">{link.form}</KeyValue> : null}
          {link.company_cik ? (
            <KeyValue label="Registrant (CIK)">
              <span className="type-id">{link.company_cik}</span>
            </KeyValue>
          ) : null}
          <KeyValue label="Period verification">
            <span className="type-id">{link.period_verification}</span>
            {verification ? <div className="type-meta mt-0.5">{verification}</div> : null}
          </KeyValue>
          <KeyValue label="Disclosure evidence">
            <span>{DISCLOSURE_WORDING[disclosure.level].label}</span>
            <div className="type-meta mt-0.5">{DISCLOSURE_WORDING[disclosure.level].sentence}</div>
            {disclosure.items !== null ? (
              <div className="type-meta">
                Stored 8-K items: <span className="type-id">{disclosure.items === "" ? "(none stated)" : disclosure.items}</span>
              </div>
            ) : null}
            <div className="type-meta">{DISCLOSURE_CAVEAT}</div>
          </KeyValue>
        </KeyValueList>
        <RefusalReference scopes={["FILING_LINK"]} refusals={refusals} />
      </Frame>

      {accounting.state === "NOT_REQUESTED" ? (
        <RefusalNotice
          kind="NOT_REQUESTED"
          title="Accounting"
          reason="No filing was supplied, so no accounting was evaluated. A filing is never chosen for you, even from the company or the dates."
        >
          <a href={configHref} className="text-accent hover:underline">
            Supply a filing accession and both EPS selectors
          </a>
        </RefusalNotice>
      ) : accounting.state === "LINK_REFUSED" ? (
        <RefusalNotice kind="REFUSED" title="Accounting" reason={`The filing link was refused (${link.status}), so the accounting was not evaluated.`}>
          <span className="type-id text-text-3">{accounting.state}</span> · {ACCOUNTING_STATE_LABEL[accounting.state]}
        </RefusalNotice>
      ) : accounting.eps !== null ? (
        <>
          <p className="type-meta">
            Accounting: <span className="type-id">{accounting.state}</span>. Descriptive accounting data from the supplied filing; it carries no point-in-time claim.
          </p>
          <RequestSummary eps={accounting.eps} />
          <CurrentEpsPanel current={accounting.eps.current} />
          <ComparativeEpsPanel comparative={accounting.eps.comparative} />
          <EpsChangePanel change={accounting.eps.change} />
          <RefusalReference scopes={["ACCOUNTING"]} refusals={refusals} />
        </>
      ) : (
        <Callout role="note" label="Accounting">
          <p>No accounting figures.</p>
        </Callout>
      )}
    </div>
  );
}
