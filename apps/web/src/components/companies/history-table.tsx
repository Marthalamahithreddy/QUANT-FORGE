import { ChangeFigure, Figure } from "@/components/companies/figure";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import type { FilingFinancials, MetricId } from "@/lib/api/schema";
import { METRIC_IDS } from "@/lib/api/schema";
import { METRIC_LABEL, METRIC_UNIT, fiscalLabel } from "@/lib/companies-view";
import { formatDate } from "@/lib/format";

function columnsFor(metric: MetricId): DataTableColumn<FilingFinancials>[] {
  const section = (filing: FilingFinancials) => filing.metrics.find((m) => m.metric === metric);
  return [
    {
      id: `${metric}-current`,
      header: `${METRIC_LABEL[metric]}, ${METRIC_UNIT[metric]}`,
      align: "end",
      cell: (filing) => {
        const found = section(filing);
        return found ? <Figure point={found.current} metric={metric} /> : null;
      },
    },
    {
      id: `${metric}-change`,
      header: "vs prior year",
      priority: "P2",
      cell: (filing) => {
        const found = section(filing);
        return found ? <ChangeFigure section={found} /> : null;
      },
    },
  ];
}

/** Every stored filing, newest period first, as the API ordered them. Nothing is sorted or filled in here. */
export function HistoryTable({ filings }: { readonly filings: readonly FilingFinancials[] }) {
  const columns: DataTableColumn<FilingFinancials>[] = [
    { id: "period", header: "Period", cell: (filing) => <a href={`#evidence-${filing.accession}`} className="text-accent hover:underline">{fiscalLabel(filing)}</a> },
    { id: "form", header: "Form", cell: (filing) => <span className="type-id">{filing.form}</span> },
    { id: "period_end", header: "Period end", priority: "P2", cell: (filing) => <span className="num">{formatDate(filing.period_end)}</span> },
    ...METRIC_IDS.flatMap(columnsFor),
  ];
  return (
    <DataTable
      caption="Revenue (customer contracts), net income (parent) and diluted EPS per stored filing"
      captionHidden
      columns={columns}
      rows={filings}
      rowKey={(filing) => filing.accession}
    />
  );
}
