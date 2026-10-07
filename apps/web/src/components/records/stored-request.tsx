import { AppLink } from "@/components/ui/app-link";

import { Id } from "@/components/records/identifier";
import { DomainState } from "@/components/state/domain-state";
import { Section } from "@/components/ui/section";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import type { RecordDetail } from "@/lib/api/record-types";
import { EPS_KIND_LABEL, PERIOD_CLASS_LABEL } from "@/lib/filings-view";

const NotPinned = ({ reason }: { readonly reason: string }) => <DomainState kind="NOT_REQUESTED" reason={reason} />;

/**
 * The request exactly as the manifest stored it (the effective inputs after defaults): what was
 * asked, not what a new request would be today. Only stored fields appear; an input the request
 * did not pin is said to be not requested, never filled in.
 */
export function StoredRequest({ record }: { readonly record: RecordDetail }) {
  const request = record.manifest.request;
  return (
    <Section id="section-request" title="Stored request" note="The effective inputs after defaults, as stored.">
      <KeyValueList>
        <KeyValue label="Event">
          <Id value={request.event_id} />
          <div className="type-meta">
            <AppLink href={`/earnings/${request.event_id}`} className="text-accent hover:underline">
              Open the current event record
            </AppLink>{" "}
            (the event as stored now, not part of this baseline).
          </div>
        </KeyValue>
        <KeyValue label="Horizons">
          <span className="num">{request.horizons.map((value) => `+${value}`).join(", ")}</span> sessions after the anchor session
        </KeyValue>
        <KeyValue label="Price basis">
          <span className="type-id">{request.price_basis}</span>
        </KeyValue>
        <KeyValue label="Research modes">
          <span className="type-id">{request.modes.join(", ")}</span>
        </KeyValue>
        <KeyValue label="Benchmark">
          {request.benchmark_id === null ? <NotPinned reason="No benchmark was selected for this request." /> : <Id value={request.benchmark_id} />}
        </KeyValue>
        <KeyValue label="Filing accession">
          {request.filing_accession === null ? <NotPinned reason="No filing was supplied for this request." /> : <span className="type-id">{request.filing_accession}</span>}
        </KeyValue>
        {request.filing_accession !== null ? (
          <>
            <KeyValue label="EPS period class">{request.eps_period_class ? PERIOD_CLASS_LABEL[request.eps_period_class] : <NotPinned reason="Not stated." />}</KeyValue>
            <KeyValue label="EPS kind">{request.eps_kind ? EPS_KIND_LABEL[request.eps_kind] : <NotPinned reason="Not stated." />}</KeyValue>
          </>
        ) : null}
        <KeyValue label="Pinned security">{request.security_id === null ? <NotPinned reason="No security was pinned; the listing was resolved from the event." /> : <Id value={request.security_id} />}</KeyValue>
        <KeyValue label="Stock price source">{request.stock_source_id === null ? <NotPinned reason="No price source was pinned." /> : <Id value={request.stock_source_id} />}</KeyValue>
        <KeyValue label="Benchmark price source">
          {request.benchmark_source_id === null ? <NotPinned reason="No benchmark price source was pinned." /> : <Id value={request.benchmark_source_id} />}
        </KeyValue>
      </KeyValueList>
    </Section>
  );
}
