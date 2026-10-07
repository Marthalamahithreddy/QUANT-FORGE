
import { BlockLink, OVERVIEW_ROWS } from "@/components/overview/overview-block";
import { RecordLink } from "@/components/records/record-link";
import { RecordFailure } from "@/components/records/record-failure";
import { ReproducibilityBadge } from "@/components/state/reproducibility-badge";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { getRecordPage } from "@/lib/api/records";
import type { RecordSummary } from "@/lib/api/schema";
import { formatUtcInstant } from "@/lib/format";
import { SAVED_PATH } from "@/lib/record-view";
import { gapSummary } from "@/lib/record-wording";

const COLUMNS: readonly DataTableColumn<RecordSummary>[] = [
  { id: "record", header: "Record", cell: (record) => <RecordLink record={record} /> },
  { id: "saved", header: "Saved (UTC)", cell: (record) => <span className="num whitespace-nowrap">{formatUtcInstant(record.created_at)}</span> },
  {
    id: "reproducibility",
    header: "Reproducibility",
    cell: (record) => (
      <span className="flex flex-col items-start gap-1">
        <ReproducibilityBadge level={record.reproducibility_level} />
        {record.reproducibility_gaps.length > 0 ? <span className="type-meta">Gaps: {gapSummary(record.reproducibility_gaps)}</span> : null}
      </span>
    ),
  },
];

/**
 * The newest saved research records: ONE list request. It opens no record, reads no event and
 * requests no verification; it cannot create a record, and says so rather than offering to.
 */
export async function RecentRecords() {
  const result = await getRecordPage({ limit: OVERVIEW_ROWS, offset: 0 });
  if (result.kind !== "ok") {
    return <RecordFailure failure={result} subject="the saved research records" retryHref="/" />;
  }
  const { items, total } = result.data;
  if (items.length === 0) {
    return (
      <EmptyState title="No saved research records are stored">
        This interface lists records that already exist; it cannot save one. Nothing is shown in the meantime.
      </EmptyState>
    );
  }
  return (
    <>
      <DataTable captionHidden caption="Recent saved research records" columns={COLUMNS} rows={items} rowKey={(record) => record.record_id} />
      <p className="type-meta">
        Showing {items.length} of {total} saved research records.
      </p>
      <BlockLink href={SAVED_PATH}>Open Saved research</BlockLink>
    </>
  );
}
