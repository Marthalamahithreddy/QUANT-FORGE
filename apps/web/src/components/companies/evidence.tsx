import { Figure } from "@/components/companies/figure";
import { AppLink } from "@/components/ui/app-link";
import { CopyValue } from "@/components/ui/copy-value";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import { Section } from "@/components/ui/section";
import type { FactSource, FilingFinancials, MetricId } from "@/lib/api/schema";
import { METRIC_LABEL, PERIOD_CLASS_NOUN, fiscalLabel } from "@/lib/companies-view";
import { filingDetailHref } from "@/lib/filings-view";
import { formatDate, formatUtcInstant } from "@/lib/format";

interface FactRow {
  readonly key: string;
  readonly metric: MetricId;
  readonly role: string;
  readonly fact: FactSource;
}

function factRows(filing: FilingFinancials): FactRow[] {
  return filing.metrics.flatMap((section) =>
    (
      [
        ["current", "Current period", section.current.fact],
        ["prior", "Prior year, as presented in this filing", section.prior.fact],
      ] as const
    ).flatMap(([key, role, fact]) => (fact === null ? [] : [{ key: `${section.metric}-${key}`, metric: section.metric, role, fact }])),
  );
}

const FACT_COLUMNS: readonly DataTableColumn<FactRow>[] = [
  { id: "metric", header: "Figure", cell: (row) => `${METRIC_LABEL[row.metric]} · ${row.role}` },
  { id: "value", header: "Exact value", align: "end", cell: (row) => <span className="num">{row.fact.value ?? "—"}</span> },
  { id: "unit", header: "Unit", cell: (row) => <span className="type-id">{row.fact.unit}</span> },
  { id: "period", header: "Period end", cell: (row) => <span className="num">{formatDate(row.fact.period_end)}</span> },
  { id: "qtrs", header: "Quarters", priority: "P2", align: "end", cell: (row) => row.fact.qtrs },
  { id: "tag", header: "SEC concept", priority: "P2", cell: (row) => <span className="type-id">{`${row.fact.tag} (${row.fact.taxonomy_version})`}</span> },
  { id: "fact", header: "Fact id", priority: "P3", cell: (row) => <span className="type-id">{row.fact.fact_id}</span> },
];

/** Where one filing's figures came from: the filing, its acceptance, the stored package and each fact. */
export function FilingEvidence({ filing, cik }: { readonly filing: FilingFinancials; readonly cik: string }) {
  const title = `${fiscalLabel(filing)} · ${filing.form}`;
  return (
    <Section
      id={`evidence-${filing.accession}`}
      title={`Evidence: ${title}`}
      note={`The ${PERIOD_CLASS_NOUN[filing.period_class]} figures above come from this filing.`}
    >
      <KeyValueList>
        <KeyValue label="Filing">
          <AppLink
            href={filingDetailHref({ accession: filing.accession, form: filing.form, company_cik: cik })}
            className="type-id text-accent underline-offset-2 hover:underline"
          >
            {filing.accession}
          </AppLink>{" "}
          <span className="type-meta">(EPS detail and period checks)</span>
        </KeyValue>
        <KeyValue label="Form">{filing.form}</KeyValue>
        <KeyValue label="Reporting period">
          {PERIOD_CLASS_NOUN[filing.period_class]} ended {formatDate(filing.period_end)}
        </KeyValue>
        <KeyValue label="Filing date">
          {formatDate(filing.filing_date)} <span className="type-meta">(source metadata, not an availability time)</span>
        </KeyValue>
        <KeyValue label="Report date">{formatDate(filing.report_date)}</KeyValue>
        <KeyValue label="Stored from">
          SEC Financial Statement Data Sets package <span className="type-id">{filing.source_artifact}</span>, stored by QuantForge{" "}
          {formatUtcInstant(filing.package_stored_at)}{" "}
          <span className="type-meta">(storage time; not when a figure was known or tradable)</span>
        </KeyValue>
        <KeyValue label="Package SHA-256">
          <span className="type-id break-all">{filing.source_artifact_sha256}</span>{" "}
          <CopyValue value={filing.source_artifact_sha256} label="package SHA-256" />
        </KeyValue>
        <KeyValue label="On SEC EDGAR">
          <a href={filing.source_url} rel="noopener noreferrer" target="_blank" className="text-accent underline underline-offset-2">
            Open the filing index on sec.gov
          </a>{" "}
          <span className="type-meta">(a link only: QuantForge did not fetch it)</span>
        </KeyValue>
      </KeyValueList>
      <DataTable
        caption={`Stored facts behind ${title}`}
        columns={FACT_COLUMNS}
        rows={factRows(filing)}
        rowKey={(row) => row.key}
        empty={<p className="type-meta">No figure of this filing could be selected, so no fact is shown. The refusals are listed above.</p>}
      />
      <ul className="type-meta flex flex-col gap-0.5">
        {filing.metrics.flatMap((section) =>
          (["current", "prior"] as const)
            .filter((side) => section[side].status !== "AVAILABLE")
            .map((side) => (
              <li key={`${section.metric}-${side}`}>
                {METRIC_LABEL[section.metric]} ({side === "current" ? "current" : "prior year"}): <Figure point={section[side]} metric={section.metric} />{" "}
                {section[side].reason}
              </li>
            )),
        )}
      </ul>
    </Section>
  );
}
