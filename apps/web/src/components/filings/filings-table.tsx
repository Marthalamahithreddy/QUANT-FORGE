import Link from "next/link";

import { MISSING, formatDate } from "@/lib/format";
import { filingDetailHref } from "@/lib/filings-view";
import type { FilingIndexItem } from "@/types/filings";

const CELL = "px-3 py-1.5 align-top";

function DateCell({ value }: { readonly value: string | null }) {
  return value === null ? (
    <span className="text-text-muted">
      <span aria-hidden="true">{MISSING}</span>
      <span className="sr-only">Not stated</span>
    </span>
  ) : (
    formatDate(value)
  );
}

export interface FilingsTableProps {
  readonly filings: readonly FilingIndexItem[];
  /** Offset of the first row, so a row key never depends on the (repeatable) accession. */
  readonly offset: number;
}

/**
 * One loaded page of the filings index, in the API's order.
 *
 * Nothing is sorted, filtered or de-duplicated here: a joint accession stored under two
 * registrants is two rows, told apart by CIK.
 */
export function FilingsTable({ filings, offset }: FilingsTableProps) {
  return (
    <div
      // Focusable so keyboard users can scroll the rows at narrow widths.
      tabIndex={0}
      role="region"
      aria-label="Filings table, scrollable"
      className="overflow-auto rounded-md border border-border-subtle"
    >
      <table className="font-tabular w-full border-collapse text-xs">
        <caption className="sr-only">
          Stored filings that have SEC FSDS financial-fact data, newest filing
          date first.
        </caption>
        <thead className="bg-surface-2">
          <tr className="border-b border-border-strong text-left text-text-muted">
            {[
              "Filing date",
              "Form",
              "CIK",
              "Accession",
              "Report date",
              "FSDS period end",
            ].map((label) => (
              <th
                key={label}
                scope="col"
                className="px-3 py-2 font-medium whitespace-nowrap"
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {filings.map((filing, index) => (
            <tr
              key={`${offset + index}`}
              className="border-b border-border-subtle hover:bg-surface-1"
            >
              <td className={`${CELL} whitespace-nowrap`}>
                <DateCell value={filing.filing_date} />
              </td>
              <td className={`${CELL} font-mono whitespace-nowrap`}>
                {filing.form}
              </td>
              <td className={`${CELL} font-mono whitespace-nowrap`}>
                {filing.company_cik ?? (
                  <span className="font-sans text-text-muted">
                    <span aria-hidden="true">{MISSING}</span>
                    <span className="sr-only">Not stated</span>
                  </span>
                )}
              </td>
              <td className={`${CELL} font-mono whitespace-nowrap`}>
                <Link
                  href={filingDetailHref(filing)}
                  className="text-accent underline-offset-2 hover:underline"
                >
                  {filing.accession}
                </Link>
              </td>
              <td className={`${CELL} whitespace-nowrap`}>
                <DateCell value={filing.report_date} />
              </td>
              <td className={`${CELL} whitespace-nowrap`}>
                <DateCell value={filing.fsds_period_end} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
