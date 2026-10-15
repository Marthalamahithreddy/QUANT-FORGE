import { DomainState } from "@/components/state/domain-state";
import { AppLink } from "@/components/ui/app-link";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import type { CompanyDirectoryItem } from "@/lib/api/schema";
import { companyHref } from "@/lib/companies-view";

const COLUMNS: readonly DataTableColumn<CompanyDirectoryItem>[] = [
  {
    id: "name",
    header: "Company",
    cell: (company) => (
      <AppLink href={companyHref(company.cik)} className="text-accent underline-offset-2 hover:underline">
        {company.name}
      </AppLink>
    ),
  },
  {
    id: "ticker",
    header: "Ticker",
    cell: (company) =>
      company.tickers.length === 0 ? <DomainState kind="NOT_STATED" compact /> : <span className="type-id">{company.tickers.join(", ")}</span>,
  },
  { id: "cik", header: "CIK", priority: "P2", cell: (company) => <span className="type-id">{company.cik}</span> },
  {
    id: "financials",
    header: "Filings with financial data",
    align: "end",
    cell: (company) =>
      company.filings_with_financial_facts === 0 ? (
        <DomainState kind="MISSING" reason="No SEC financial facts are stored for this company." />
      ) : (
        <span>
          {company.filings_with_financial_facts}
          <span className="type-meta"> of {company.stored_filings} stored</span>
        </span>
      ),
  },
];

/** One page of the directory in the API's order (display name). Nothing is sorted or filtered here. */
export function CompaniesTable({ companies, offset }: { readonly companies: readonly CompanyDirectoryItem[]; readonly offset: number }) {
  return <DataTable caption="Stored companies" columns={COLUMNS} rows={companies} rowKey={(_, index) => String(offset + index)} />;
}
