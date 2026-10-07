
import { AccessionLink } from "@/components/filings/accession-link";
import { BlockLink, OVERVIEW_ROWS } from "@/components/overview/overview-block";
import { DomainState } from "@/components/state/domain-state";
import { ApiFailureState } from "@/components/ui/api-failure";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { getFilings } from "@/lib/api/filings";
import type { FilingIndexItem } from "@/lib/api/schema";
import { formatDate } from "@/lib/format";

const COLUMNS: readonly DataTableColumn<FilingIndexItem>[] = [
  { id: "accession", header: "Accession", cell: (filing) => <AccessionLink filing={filing} /> },
  { id: "form", header: "Form", cell: (filing) => <span className="type-id whitespace-nowrap">{filing.form}</span> },
  {
    id: "filing_date",
    header: "Filing date",
    cell: (filing) =>
      filing.filing_date === null ? <DomainState kind="NOT_STATED" compact /> : <span className="num whitespace-nowrap">{formatDate(filing.filing_date)}</span>,
  },
  {
    id: "cik",
    header: "CIK",
    priority: "P2",
    cell: (filing) => (filing.company_cik === null ? <DomainState kind="NOT_STATED" compact /> : <span className="type-id">{filing.company_cik}</span>),
  },
];

/** The newest stored filings with FSDS data: ONE list request, in the API's order, no detail reads. */
export async function LatestFilings() {
  const result = await getFilings({ limit: OVERVIEW_ROWS });
  if (result.kind !== "ok") {
    return <ApiFailureState failure={result} subject="the latest filings" retryHref="/" />;
  }
  const { items, total } = result.data;
  if (items.length === 0) {
    return (
      <EmptyState title="No FSDS-backed filings are stored">
        Filings appear here once SEC FSDS financial facts have been ingested for them. This does not mean no filings exist.
      </EmptyState>
    );
  }
  return (
    <>
      <DataTable captionHidden caption="Latest stored filings" columns={COLUMNS} rows={items} rowKey={(filing, index) => `${filing.accession}:${filing.company_cik ?? "none"}:${index}`} />
      <p className="type-meta">
        Showing {items.length} of {total} stored filings with FSDS data. Filing dates are source metadata.
      </p>
      <BlockLink href="/filings">Open Filings</BlockLink>
    </>
  );
}
