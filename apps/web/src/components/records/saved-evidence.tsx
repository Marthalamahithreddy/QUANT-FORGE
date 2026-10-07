import type { ReactNode } from "react";

import { FinancialValue } from "@/components/financial/financial-value";
import { Id } from "@/components/records/identifier";
import { DomainState } from "@/components/state/domain-state";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EvidenceDrawer } from "@/components/ui/evidence-drawer";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import { Section } from "@/components/ui/section";
import type {
  AccountingEvidenceManifest,
  BarPin,
  BenchmarkEvidenceManifest,
  EvidenceState,
  FilingEvidenceManifest,
  ManifestEvidence,
  StockEvidenceManifest,
} from "@/lib/api/record-types";
import { BASIS_LABEL, BASIS_MEANING, TIMING_LABEL } from "@/lib/availability";
import { formatDate, formatUtcInstant } from "@/lib/format";
import { EVIDENCE_STATE_LABEL, EVIDENCE_STATE_SENTENCE } from "@/lib/record-wording";
import { DISCLOSURE_WORDING, LISTING_LABEL } from "@/lib/research-wording";

const None = ({ reason }: { readonly reason: string }) => <DomainState kind="NOT_STATED" reason={reason} />;
const Run = ({ id }: { readonly id: string | null }) => (id === null ? <None reason="No ingestion run is recorded for this." /> : <Id value={id} />);

/**
 * What the baseline's manifest says each part of the study CONSUMED, as stored. The kinds of
 * reference stay apart: an identity is not a run, a run is not an artifact hash, a fingerprint is
 * not a source, a provider-stated availability time is not the time this system stored the row,
 * and a session digest is not a bar. Only fields the manifest holds appear; a source or run
 * identifier is shown as the identifier (the API supplies no name for it).
 */

function EventEvidenceBody({ evidence }: { readonly evidence: ManifestEvidence }) {
  const { event } = evidence;
  return (
    <KeyValueList>
      <KeyValue label="Event id">
        <Id value={event.event_id} />
      </KeyValue>
      <KeyValue label="Event fingerprint">
        <Id value={event.fingerprint} />
      </KeyValue>
      <KeyValue label="Event date">{formatDate(event.event_date)}</KeyValue>
      <KeyValue label="Timing">{TIMING_LABEL[event.timing]}</KeyValue>
      <KeyValue label="Source id">
        <Id value={event.source_id} />
        <div className="type-meta">Source name not available from the API.</div>
      </KeyValue>
      <KeyValue label="Ingestion run">
        <Run id={event.ingestion_run_id} />
      </KeyValue>
    </KeyValueList>
  );
}

function ListingEvidenceBody({ evidence }: { readonly evidence: ManifestEvidence }) {
  return (
    <KeyValueList>
      <KeyValue label="Resolution">{LISTING_LABEL[evidence.listing_resolution_status]}</KeyValue>
      <KeyValue label="Resolution code">
        <span className="type-id">{evidence.listing_resolution_status}</span>
      </KeyValue>
      <KeyValue label="Listing evidence">
        {evidence.listing_evidence === null ? <None reason="No listing evidence kind is stored." /> : <span className="type-id">{evidence.listing_evidence}</span>}
      </KeyValue>
      <KeyValue label="Listing id">{evidence.stock.listing_id === null ? <None reason="No listing was resolved." /> : <Id value={evidence.stock.listing_id} />}</KeyValue>
      <KeyValue label="Security id">{evidence.stock.security_id === null ? <None reason="No security was resolved." /> : <Id value={evidence.stock.security_id} />}</KeyValue>
    </KeyValueList>
  );
}

function PitEvidenceBody({ evidence }: { readonly evidence: ManifestEvidence }) {
  const { pit } = evidence;
  return (
    <KeyValueList>
      <KeyValue label="Eligibility">
        <span className="type-id">{pit.eligibility}</span>
      </KeyValue>
      <KeyValue label="Provider-stated availability time">
        {pit.observed_at === null ? (
          <None reason="The source did not supply a historical availability time." />
        ) : (
          <>
            {formatUtcInstant(pit.observed_at)}
            {pit.observed_at_basis ? (
              <div className="type-meta">
                Basis: {BASIS_LABEL[pit.observed_at_basis]}. {BASIS_MEANING[pit.observed_at_basis]}
              </div>
            ) : null}
          </>
        )}
      </KeyValue>
    </KeyValueList>
  );
}

const barColumns: DataTableColumn<BarPin>[] = [
  { id: "session", header: "Session", cell: (bar) => <span className="num whitespace-nowrap">{formatDate(bar.session)}</span> },
  { id: "close", header: "Close", align: "end", cell: (bar) => <FinancialValue kind="price" value={bar.close} /> },
  { id: "run", header: "Ingestion run", cell: (bar) => <Run id={bar.ingestion_run_id} /> },
  { id: "bar", header: "Bar id", cell: (bar) => <Id value={bar.bar_id} /> },
  { id: "source", header: "Source id", cell: (bar) => <Id value={bar.source_id} /> },
  { id: "stored", header: "Stored by this system at", cell: (bar) => <span className="num whitespace-nowrap">{formatUtcInstant(bar.ingested_at)}</span> },
];

function SessionsFacts({ window, count, digest }: { readonly window: readonly [string, string] | null; readonly count: number | null; readonly digest: string | null }) {
  return (
    <>
      <KeyValue label="Stored-session window">
        {window === null ? <None reason="No session window was read." /> : <span className="num">{formatDate(window[0])} to {formatDate(window[1])}</span>}
      </KeyValue>
      <KeyValue label="Stored sessions in window">{count === null ? <None reason="Not read." /> : <span className="num">{count}</span>}</KeyValue>
      <KeyValue label="Session dates digest">
        {digest === null ? <None reason="Not read." /> : <Id value={digest} />}
        <div className="type-meta">A digest of the stored session dates the study could see. It is not a bar and not a price.</div>
      </KeyValue>
    </>
  );
}

function StateRow({ state }: { readonly state: EvidenceState }) {
  return (
    <KeyValue label="Evidence state">
      {EVIDENCE_STATE_LABEL[state]} <span className="type-id text-text-3">{state}</span>
      <div className="type-meta">{EVIDENCE_STATE_SENTENCE[state]}</div>
    </KeyValue>
  );
}

function StockEvidenceBody({ stock }: { readonly stock: StockEvidenceManifest }) {
  return (
    <div className="flex flex-col gap-3">
      <KeyValueList>
        <StateRow state={stock.state} />
        <KeyValue label="Price basis">
          <span className="type-id">{stock.price_basis}</span>
        </KeyValue>
        <KeyValue label="Price source id">{stock.price_source_id === null ? <None reason="No price source was used." /> : <Id value={stock.price_source_id} />}</KeyValue>
        <SessionsFacts
          window={stock.sessions ? [stock.sessions.window_start, stock.sessions.window_end] : null}
          count={stock.sessions?.session_count ?? null}
          digest={stock.sessions?.sessions_sha256 ?? null}
        />
      </KeyValueList>
      <h3 className="text-xs font-medium tracking-wide text-text-2 uppercase">Consumed bars ({stock.bars.length})</h3>
      <DataTable caption="Consumed stock price bars" columns={barColumns} rows={stock.bars} rowKey={(bar) => bar.bar_id} empty={<p className="type-meta">No bar was consumed.</p>} />
    </div>
  );
}

function BenchmarkEvidenceBody({ benchmark }: { readonly benchmark: BenchmarkEvidenceManifest }) {
  return (
    <div className="flex flex-col gap-3">
      <KeyValueList>
        <StateRow state={benchmark.state} />
        <KeyValue label="Benchmark id">{benchmark.benchmark_id === null ? <None reason="No benchmark was requested." /> : <Id value={benchmark.benchmark_id} />}</KeyValue>
        <KeyValue label="Price basis">
          <span className="type-id">{benchmark.price_basis}</span>
        </KeyValue>
        <SessionsFacts
          window={benchmark.window_start && benchmark.window_end ? [benchmark.window_start, benchmark.window_end] : null}
          count={benchmark.session_count}
          digest={benchmark.sessions_sha256}
        />
      </KeyValueList>
      <h3 className="text-xs font-medium tracking-wide text-text-2 uppercase">Consumed levels ({benchmark.bars.length})</h3>
      <DataTable caption="Consumed benchmark levels" columns={barColumns} rows={benchmark.bars} rowKey={(bar) => bar.bar_id} empty={<p className="type-meta">No level was consumed.</p>} />
    </div>
  );
}

function FilingEvidenceBody({ filing }: { readonly filing: FilingEvidenceManifest }) {
  return (
    <KeyValueList>
      <StateRow state={filing.state} />
      <KeyValue label="Accession">{filing.accession_number === null ? <None reason="No filing was supplied." /> : <span className="type-id">{filing.accession_number}</span>}</KeyValue>
      <KeyValue label="Form">{filing.form ?? <None reason="No form is stored." />}</KeyValue>
      <KeyValue label="Source id">{filing.source_id === null ? <None reason="No source is recorded." /> : <Id value={filing.source_id} />}</KeyValue>
      <KeyValue label="Ingestion run">
        <Run id={filing.ingestion_run_id} />
      </KeyValue>
      <KeyValue label="Disclosure evidence">
        {DISCLOSURE_WORDING[filing.disclosure_level].label}
        <div className="type-meta">{DISCLOSURE_WORDING[filing.disclosure_level].sentence}</div>
        {filing.items !== null ? (
          <div className="type-meta">
            Stored 8-K items: <span className="type-id">{filing.items === "" ? "(none stated)" : filing.items}</span>
          </div>
        ) : null}
        <div className="type-meta">It carries no time and does not make the event point-in-time eligible.</div>
      </KeyValue>
    </KeyValueList>
  );
}

function AccountingEvidenceBody({ accounting }: { readonly accounting: AccountingEvidenceManifest }) {
  return (
    <div className="flex flex-col gap-3">
      <KeyValueList>
        <StateRow state={accounting.state} />
        <KeyValue label="FSDS packages">
          {accounting.artifacts.length === 0 ? (
            <None reason="No FSDS package carrying this filing's facts is stored." />
          ) : (
            <ul className="flex flex-col gap-1.5">
              {accounting.artifacts.map(([name, sha]) => (
                <li key={`${name}-${sha}`}>
                  {name}
                  <div className="type-meta">
                    SHA-256 <Id value={sha} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </KeyValue>
      </KeyValueList>
      <h3 className="text-xs font-medium tracking-wide text-text-2 uppercase">Pinned facts ({accounting.facts.length})</h3>
      {accounting.facts.length === 0 ? (
        <p className="type-meta">No fact is pinned.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {accounting.facts.map((fact) => (
            <li key={fact.fact_id}>
              <KeyValueList>
                <KeyValue label="Role">{fact.role}</KeyValue>
                <KeyValue label="Fact id">
                  <Id value={fact.fact_id} />
                </KeyValue>
                <KeyValue label="Tag">
                  <span className="type-id">{fact.tag}</span>
                </KeyValue>
                <KeyValue label="Taxonomy version">
                  <span className="type-id">{fact.taxonomy_version}</span>
                </KeyValue>
                <KeyValue label="Value">
                  <FinancialValue kind="eps" value={fact.value} />
                  <span className="type-meta"> {fact.unit}</span>
                </KeyValue>
                <KeyValue label="Period end">{formatDate(fact.period_end)}</KeyValue>
                <KeyValue label="Artifact SHA-256">
                  <Id value={fact.artifact_sha256} />
                </KeyValue>
              </KeyValueList>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface EvidenceRow {
  readonly id: string;
  readonly name: string;
  readonly state: ReactNode;
  readonly summary: ReactNode;
  readonly title: string;
  readonly body: ReactNode;
}

const stateText = (state: EvidenceState) => (
  <>
    {EVIDENCE_STATE_LABEL[state]} <span className="type-id text-text-3">{state}</span>
  </>
);
const recorded = <span className="text-text-2">Recorded</span>;

function rows(evidence: ManifestEvidence): readonly EvidenceRow[] {
  const { stock, benchmark, filing, accounting } = evidence;
  return [
    {
      id: "event",
      name: "Event",
      state: recorded,
      summary: evidence.event.ingestion_run_id === null ? "No ingestion run recorded" : "Ingestion run recorded",
      title: "Event evidence",
      body: <EventEvidenceBody evidence={evidence} />,
    },
    {
      id: "listing",
      name: "Listing resolution",
      state: recorded,
      summary: LISTING_LABEL[evidence.listing_resolution_status],
      title: "Listing evidence",
      body: <ListingEvidenceBody evidence={evidence} />,
    },
    {
      id: "pit",
      name: "Point-in-time inputs",
      state: recorded,
      summary: evidence.pit.observed_at === null ? "No provider-stated availability time" : "Provider-stated availability time stored",
      title: "Point-in-time evidence",
      body: <PitEvidenceBody evidence={evidence} />,
    },
    {
      id: "stock",
      name: "Stock prices",
      state: stateText(stock.state),
      summary: `${stock.bars.length} bar${stock.bars.length === 1 ? "" : "s"} consumed`,
      title: "Stock price evidence",
      body: <StockEvidenceBody stock={stock} />,
    },
    {
      id: "benchmark",
      name: "Benchmark prices",
      state: stateText(benchmark.state),
      summary: `${benchmark.bars.length} level${benchmark.bars.length === 1 ? "" : "s"} consumed`,
      title: "Benchmark price evidence",
      body: <BenchmarkEvidenceBody benchmark={benchmark} />,
    },
    {
      id: "filing",
      name: "Filing",
      state: stateText(filing.state),
      summary: filing.accession_number ?? "No filing",
      title: "Filing evidence",
      body: <FilingEvidenceBody filing={filing} />,
    },
    {
      id: "accounting",
      name: "Accounting (FSDS)",
      state: stateText(accounting.state),
      summary: `${accounting.artifacts.length} package${accounting.artifacts.length === 1 ? "" : "s"}, ${accounting.facts.length} fact${accounting.facts.length === 1 ? "" : "s"}`,
      title: "Accounting evidence",
      body: <AccountingEvidenceBody accounting={accounting} />,
    },
  ];
}

const columns: DataTableColumn<EvidenceRow>[] = [
  { id: "name", header: "Evidence", cell: (row) => <span className="font-medium">{row.name}</span> },
  { id: "state", header: "State in the manifest", cell: (row) => row.state },
  { id: "summary", header: "What the study consumed", cell: (row) => row.summary },
  {
    id: "details",
    header: "Details",
    cell: (row) => (
      <EvidenceDrawer subject={`the baseline's ${row.name.toLowerCase()}`} title={row.title}>
        {row.body}
      </EvidenceDrawer>
    ),
  },
];

/**
 * The evidence the baseline consumed, one row per kind, each opening the shared evidence drawer.
 * The drawer primitive and the key/value layout are the live workspace's; the content is built from
 * the STORED manifest, because that evidence differs materially from a live observation's.
 */
export function SavedEvidence({ evidence }: { readonly evidence: ManifestEvidence }) {
  return (
    <Section id="section-evidence" title="Evidence and provenance" note="What the baseline's manifest says it consumed.">
      <DataTable caption="Evidence consumed by the stored baseline" columns={columns} rows={rows(evidence)} rowKey={(row) => row.id} />
    </Section>
  );
}
