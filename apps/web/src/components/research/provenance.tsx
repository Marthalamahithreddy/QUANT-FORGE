import { Suspense } from "react";

import { BenchmarkLookup } from "@/components/research/benchmark-lookup";
import { DomainState } from "@/components/state/domain-state";
import { EvidenceDrawer } from "@/components/ui/evidence-drawer";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import type { BenchmarkItem, EarningsEvent, Observation } from "@/lib/api/schema";
import { BASIS_LABEL, BASIS_MEANING } from "@/lib/availability";
import { formatDate, formatUtcInstant } from "@/lib/format";
import { DISCLOSURE_WORDING, DISCLOSURE_CAVEAT } from "@/lib/research-wording";

const Id = ({ value }: { readonly value: string }) => <code className="type-id break-all">{value}</code>;
const NoName = () => <span className="type-meta">Name not available from the API.</span>;
const NoRun = () => <DomainState kind="NOT_STATED" reason="No ingestion run is recorded for this." />;

/**
 * What the live observation can say about where each part came from. Only fields the API supplied
 * appear, and the kinds of reference are never merged: an identity is not a run, a run is not an
 * artifact hash, a fingerprint is not a source, and the provider-stated availability time is not
 * the time this system stored the row.
 */
export function EventEvidence({ observation, record }: { readonly observation: Observation; readonly record: EarningsEvent }) {
  const { event, provenance } = observation;
  return (
    <KeyValueList>
      <KeyValue label="Event fingerprint">
        <Id value={provenance.event_fingerprint} />
      </KeyValue>
      <KeyValue label="Source name">{record.source.name}</KeyValue>
      <KeyValue label="Provider type">
        <span className="type-id">{record.source.provider_type}</span>
      </KeyValue>
      <KeyValue label="Event source id">
        <Id value={provenance.event_source_id} />
      </KeyValue>
      <KeyValue label="Event ingestion run">{provenance.event_ingestion_run_id ? <Id value={provenance.event_ingestion_run_id} /> : <NoRun />}</KeyValue>
      <KeyValue label="Provider-stated availability time">
        {event.observed_at === null ? (
          <DomainState kind="NOT_STATED" reason="The source did not supply a historical availability time." />
        ) : (
          <>
            {formatUtcInstant(event.observed_at)}
            {event.observed_at_basis ? (
              <div className="type-meta">
                Basis: {BASIS_LABEL[event.observed_at_basis]}. {BASIS_MEANING[event.observed_at_basis]}
              </div>
            ) : null}
          </>
        )}
      </KeyValue>
      <KeyValue label="Stored by this system at">{formatUtcInstant(record.ingested_at)}</KeyValue>
    </KeyValueList>
  );
}

export function StockEvidence({ observation }: { readonly observation: Observation }) {
  const { identity, provenance } = observation;
  const stock = observation.descriptive.study.stock_returns;
  const resolution = identity.listing_resolution;
  return (
    <KeyValueList>
      <KeyValue label="Resolved listing">{resolution.listing_id ? <Id value={resolution.listing_id} /> : <DomainState kind="MISSING" reason={resolution.reason} />}</KeyValue>
      <KeyValue label="Security">{resolution.security_id ? <Id value={resolution.security_id} /> : <DomainState kind="NOT_STATED" compact />}</KeyValue>
      <KeyValue label="Exchange (MIC)">{resolution.exchange_mic ?? <DomainState kind="NOT_STATED" compact />}</KeyValue>
      <KeyValue label="Listing evidence">
        {resolution.evidence ? <span className="type-id">{resolution.evidence}</span> : <DomainState kind="NOT_STATED" compact />}
        <div className="type-meta">{resolution.reason}</div>
      </KeyValue>
      <KeyValue label="Stock price source id">
        {provenance.stock_price_source_id ? <Id value={provenance.stock_price_source_id} /> : <DomainState kind="NOT_STATED" compact />}
        <div className="type-meta">Source name not available from the API.</div>
      </KeyValue>
      <KeyValue label="Price basis">
        <span className="type-id">{observation.parameters.price_basis}</span>
      </KeyValue>
      <KeyValue label="Anchor session">{stock.anchor_session ? formatDate(stock.anchor_session) : <DomainState kind="NOT_STATED" compact />}</KeyValue>
    </KeyValueList>
  );
}

export function BenchmarkEvidence({ observation, benchmark }: { readonly observation: Observation; readonly benchmark?: BenchmarkItem }) {
  const id = observation.provenance.benchmark_id;
  if (id === null) {
    return <DomainState kind="NOT_REQUESTED" reason="No benchmark was selected, so no benchmark evidence exists for this study." />;
  }
  return (
    <KeyValueList>
      <KeyValue label="Benchmark id">
        <Id value={id} />
      </KeyValue>
      <KeyValue label="Name">{benchmark ? benchmark.name : <NoName />}</KeyValue>
      <KeyValue label="Symbol">{benchmark ? (benchmark.symbol ?? <DomainState kind="NOT_STATED" compact />) : <NoName />}</KeyValue>
      <KeyValue label="Currency">{benchmark ? (benchmark.currency ?? <DomainState kind="NOT_STATED" compact />) : <NoName />}</KeyValue>
      <KeyValue label="Status">{benchmark ? (benchmark.is_active ? "Active" : "Inactive (retained for history)") : <NoName />}</KeyValue>
      <KeyValue label="Benchmark ingestion run">
        <DomainState kind="NOT_STATED" reason="The observation does not carry a benchmark ingestion run." />
      </KeyValue>
    </KeyValueList>
  );
}

export function FilingEvidence({ observation }: { readonly observation: Observation }) {
  const { filing_link: link, disclosure_evidence: disclosure, provenance } = observation;
  if (link.status === "NOT_LINKED") {
    return <DomainState kind="NOT_REQUESTED" reason="No filing was supplied, so no filing evidence exists for this study." />;
  }
  return (
    <KeyValueList>
      <KeyValue label="Accession">{link.accession_number ? <Id value={link.accession_number} /> : <DomainState kind="NOT_STATED" compact />}</KeyValue>
      <KeyValue label="Form">{link.form ?? <DomainState kind="NOT_STATED" compact />}</KeyValue>
      <KeyValue label="Registrant (CIK)">{link.company_cik ?? <DomainState kind="NOT_STATED" compact />}</KeyValue>
      <KeyValue label="Filing source id">{provenance.filing_source_id ? <Id value={provenance.filing_source_id} /> : <DomainState kind="NOT_STATED" compact />}</KeyValue>
      <KeyValue label="Filing ingestion run">{provenance.filing_ingestion_run_id ? <Id value={provenance.filing_ingestion_run_id} /> : <NoRun />}</KeyValue>
      <KeyValue label="Disclosure evidence">
        {DISCLOSURE_WORDING[disclosure.level].label}
        {disclosure.items !== null ? <div className="type-meta">Stored 8-K items: {disclosure.items}</div> : null}
        <div className="type-meta">{DISCLOSURE_CAVEAT}</div>
      </KeyValue>
    </KeyValueList>
  );
}

export function AccountingEvidence({ observation }: { readonly observation: Observation }) {
  const artifacts = observation.provenance.accounting_artifacts;
  const eps = observation.accounting.eps;
  if (eps === null) return <DomainState kind="NOT_REQUESTED" reason="No accounting was evaluated, so there are no facts or artifacts." />;
  const facts = [
    ["Current EPS fact", eps.current.fact],
    ["Comparative EPS fact", eps.comparative.fact],
  ] as const;
  return (
    <div className="flex flex-col gap-4">
      {facts.map(([title, fact]) =>
        fact === null ? null : (
          <div key={title} className="flex flex-col gap-2">
            <h3 className="text-xs font-medium text-text-2">{title}</h3>
            <KeyValueList>
              <KeyValue label="Fact id">
                <Id value={fact.fact_id} />
              </KeyValue>
              <KeyValue label="Tag">
                <Id value={fact.tag} />
              </KeyValue>
              <KeyValue label="Taxonomy version">
                <Id value={fact.taxonomy_version} />
              </KeyValue>
              <KeyValue label="Unit, period end, quarters">
                {fact.unit} · {formatDate(fact.period_end)} · qtrs {fact.qtrs}
              </KeyValue>
              <KeyValue label="Segments">{fact.segments === "" ? <span className="text-text-3">(empty, as stored)</span> : <code>{fact.segments}</code>}</KeyValue>
              <KeyValue label="Co-registrant">{fact.coreg === "" ? <span className="text-text-3">(empty, as stored)</span> : <code>{fact.coreg}</code>}</KeyValue>
              <KeyValue label="Exact value">{fact.value === null ? <span className="text-text-3">null</span> : <code>{fact.value}</code>}</KeyValue>
            </KeyValueList>
          </div>
        ),
      )}
      <div className="flex flex-col gap-2">
        <h3 className="text-xs font-medium text-text-2">Source artifacts (SHA-256 digests)</h3>
        {artifacts.length === 0 ? (
          <DomainState kind="NOT_STATED" reason="No artifact digest is recorded for these facts." />
        ) : (
          <KeyValueList>
            {artifacts.map((artifact) => (
              <KeyValue key={artifact.role} label={`${artifact.role === "current" ? "Current" : "Comparative"} artifact`}>
                <Id value={artifact.sha256} />
              </KeyValue>
            ))}
          </KeyValueList>
        )}
      </div>
    </div>
  );
}

/** A heading row of the provenance section with its evidence trigger. */
function Row({ title, subject, children, summary }: { readonly title: string; readonly subject: string; readonly children: React.ReactNode; readonly summary: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border-subtle py-2 last:border-b-0">
      <div className="min-w-0">
        <p className="text-sm text-text">{title}</p>
        <p className="type-meta">{summary}</p>
      </div>
      <EvidenceDrawer subject={subject} title={title}>
        {children}
      </EvidenceDrawer>
    </div>
  );
}

/**
 * The collapsed provenance section: one row per kind of evidence, each opening its drawer. The
 * live observation is NOT a saved baseline, so no reproducibility level appears here:
 * "Reproducibility is assessed when a study is saved as a baseline."
 */
export function ProvenanceSection({
  observation,
  record,
  benchmarkPage,
}: {
  readonly observation: Observation;
  readonly record: EarningsEvent;
  /** The benchmark-index page the selector is on: where a selected benchmark's name is looked up. */
  readonly benchmarkPage: number;
}) {
  const benchmarkId = observation.provenance.benchmark_id;
  return (
    <div className="flex flex-col">
      <Row title="Event" subject="the stored event" summary={`Fingerprint ${observation.provenance.event_fingerprint.slice(0, 12)}…`}>
        <EventEvidence observation={observation} record={record} />
      </Row>
      <Row title="Stock returns" subject="the stock returns" summary="Resolved listing, price source and basis">
        <StockEvidence observation={observation} />
      </Row>
      <Row title="Benchmark" subject="the benchmark" summary={observation.provenance.benchmark_id ? "The benchmark you selected" : "Not requested"}>
        {benchmarkId ? (
          <Suspense fallback={<BenchmarkEvidence observation={observation} />}>
            <BenchmarkLookup id={benchmarkId} page={benchmarkPage}>
              {(item) => <BenchmarkEvidence observation={observation} benchmark={item} />}
            </BenchmarkLookup>
          </Suspense>
        ) : (
          <BenchmarkEvidence observation={observation} />
        )}
      </Row>
      <Row title="Filing and disclosure" subject="the filing" summary={observation.filing_link.status === "NOT_LINKED" ? "Not requested" : (observation.filing_link.accession_number ?? "Filing link")}>
        <FilingEvidence observation={observation} />
      </Row>
      <Row title="Accounting facts" subject="the accounting facts" summary={observation.accounting.eps ? "Selected facts and source artifact digests" : "Not requested or refused"}>
        <AccountingEvidence observation={observation} />
      </Row>
      <p className="type-meta pt-2">Reproducibility is assessed when a study is saved as a baseline. A live study shows no level.</p>
    </div>
  );
}
