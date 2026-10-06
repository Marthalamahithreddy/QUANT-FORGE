import Link from "next/link";

import { DomainState } from "@/components/state/domain-state";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import type { FilingIndexItem } from "@/lib/api/schema";
import { filingDetailHref, isAmendment } from "@/lib/filings-view";
import { formatDate } from "@/lib/format";

/** A date the source may not have stated: NOT_STATED, never a blank or a guess. */
function DateCell({ value }: { readonly value: string | null }) {
  return value === null ? <DomainState kind="NOT_STATED" compact /> : <span className="num whitespace-nowrap">{formatDate(value)}</span>;
}

const COLUMNS: readonly DataTableColumn<FilingIndexItem>[] = [
  {
    id: "accession",
    header: "Accession",
    cell: (filing) => (
      <Link
        href={filingDetailHref(filing)}
        className="type-id text-accent underline-offset-2 hover:underline"
        aria-label={`Filing ${filing.accession}, ${filing.form}${filing.company_cik ? `, CIK ${filing.company_cik}` : ""}`}
      >
        {filing.accession}
      </Link>
    ),
  },
  {
    id: "form",
    header: "Form",
    cell: (filing) => (
      <span className="inline-flex items-center gap-2 whitespace-nowrap">
        <span className="type-id">{filing.form}</span>
        {isAmendment(filing.form) ? (
          // An amendment is a kind of filing, not a fault: a quiet tag, never an error colour.
          <span className="rounded border border-border px-1 text-[11px] leading-4 text-text-2">Amendment</span>
        ) : null}
      </span>
    ),
  },
  {
    id: "cik",
    header: "CIK",
    cell: (filing) =>
      filing.company_cik === null ? <DomainState kind="NOT_STATED" compact /> : <span className="type-id">{filing.company_cik}</span>,
  },
  { id: "filing_date", header: "Filing date", cell: (filing) => <DateCell value={filing.filing_date} /> },
  { id: "report_date", header: "Report date", priority: "P2", cell: (filing) => <DateCell value={filing.report_date} /> },
  { id: "fsds_period_end", header: "FSDS period end", priority: "P2", cell: (filing) => <DateCell value={filing.fsds_period_end} /> },
];

export interface FilingsTableProps {
  readonly filings: readonly FilingIndexItem[];
  /** Offset of the first row, so a row key never depends on the (repeatable) accession. */
  readonly offset: number;
}

/**
 * One page of the filings index, in the API's order (newest filing date first). Nothing is sorted,
 * filtered or de-duplicated here: a joint accession stored under two registrants is two rows,
 * told apart by CIK. Every listed filing has FSDS data, so there is no "FSDS available" column.
 */
export function FilingsTable({ filings, offset }: FilingsTableProps) {
  return (
    <DataTable
      caption="Stored filings with SEC FSDS data"
      columns={COLUMNS}
      rows={filings}
      rowKey={(_, index) => String(offset + index)}
    />
  );
}
