import { FinancialValue } from "@/components/financial/financial-value";
import { PercentValue } from "@/components/financial/percent-value";
import { DomainState } from "@/components/state/domain-state";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import type { NonValueState } from "@/lib/state";
import { formatDate } from "@/lib/format";
import { benchmarkRequested, horizonRows, type HorizonRow } from "@/lib/research-rows";
import { FORWARD_RETURN_LABEL } from "@/lib/research-wording";
import { abnormalReturnState, BENCHMARK_RETURN_STATE, FORWARD_RETURN_STATE } from "@/lib/state-map";
import type { Study } from "@/lib/api/schema";

const nonValue = (kind: string): NonValueState => kind as NonValueState;

const sessionCell = (value: string | null) =>
  value === null ? <DomainState kind="NOT_STATED" compact /> : <span className="num whitespace-nowrap">{formatDate(value)}</span>;

function columns(study: Study, stockOnly: boolean): DataTableColumn<HorizonRow>[] {
  const requested = benchmarkRequested(study);
  const all: DataTableColumn<HorizonRow>[] = [
    {
      id: "horizon",
      header: "Horizon",
      cell: (row) => <span className="num font-medium">+{row.horizon} sessions</span>,
    },
    {
      id: "stock",
      header: "Stock return",
      align: "end",
      cell: ({ stock }) =>
        stock.return_value !== null ? (
          <PercentValue ratio={stock.return_value} />
        ) : (
          <DomainState kind={nonValue(FORWARD_RETURN_STATE[stock.status])} reason={stock.reason} />
        ),
    },
    {
      id: "benchmark",
      header: "Benchmark return",
      align: "end",
      cell: ({ benchmark }) =>
        benchmark.benchmark_return !== null ? (
          <PercentValue ratio={benchmark.benchmark_return} />
        ) : requested ? (
          <DomainState kind={nonValue(BENCHMARK_RETURN_STATE[benchmark.status])} reason={benchmark.reason} />
        ) : (
          <DomainState kind="NOT_REQUESTED" reason="No benchmark was selected." />
        ),
    },
    {
      id: "abnormal",
      header: "Abnormal return (stock minus benchmark)",
      align: "end",
      cell: ({ abnormal, benchmark }) =>
        abnormal.abnormal_return !== null ? (
          // The backend's own figure, never recomputed here.
          <PercentValue ratio={abnormal.abnormal_return} />
        ) : (
          <DomainState
            kind={nonValue(abnormalReturnState(abnormal.status, benchmark.status))}
            reason={requested ? abnormal.reason : "No benchmark was selected."}
          />
        ),
    },
    {
      id: "status",
      header: "Stock status",
      cell: ({ stock }) => (
        <span className="whitespace-nowrap">
          {FORWARD_RETURN_LABEL[stock.status]} <span className="type-id text-text-3">{stock.status}</span>
        </span>
      ),
    },
    { id: "target_session", header: "Target session", priority: "P2", cell: ({ stock }) => sessionCell(stock.target_session) },
    { id: "anchor_session", header: "Anchor session", priority: "P2", cell: ({ stock }) => sessionCell(stock.anchor_session) },
    {
      id: "target_close",
      header: "Stock target close",
      align: "end",
      priority: "P3",
      cell: ({ stock }) => <FinancialValue kind="price" value={stock.target_close} />,
    },
    {
      id: "anchor_close",
      header: "Stock anchor close",
      align: "end",
      priority: "P3",
      cell: ({ stock }) => <FinancialValue kind="price" value={stock.anchor_close} />,
    },
    ...(requested
      ? ([
          {
            id: "bench_target_close",
            header: "Benchmark target close",
            align: "end",
            priority: "P3",
            cell: ({ benchmark }) => <FinancialValue kind="price" value={benchmark.target_close} />,
          },
          {
            id: "bench_anchor_close",
            header: "Benchmark anchor close",
            align: "end",
            priority: "P3",
            cell: ({ benchmark }) => <FinancialValue kind="price" value={benchmark.anchor_close} />,
          },
        ] satisfies DataTableColumn<HorizonRow>[])
      : []),
  ];
  // A stock-only view (the event record's preview) leaves out the columns that can only say "not requested".
  return stockOnly ? all.filter((column) => !["benchmark", "abnormal", "bench_target_close", "bench_anchor_close"].includes(column.id)) : all;
}

/**
 * The horizon-return table: the authoritative form of the event-study chart. One row per
 * configured horizon; every figure is the backend's exact string and every non-value cell says
 * which of the ten states it is (a missing target price is not a refusal, and a benchmark nobody
 * asked for is not a failure).
 */
export function ReturnsTable({ study, caption, stockOnly = false }: { readonly study: Study; readonly caption: string; readonly stockOnly?: boolean }) {
  return <DataTable caption={caption} columns={columns(study, stockOnly)} rows={horizonRows(study)} rowKey={(row) => String(row.horizon)} />;
}
