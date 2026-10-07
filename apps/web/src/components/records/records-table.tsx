import Link from "next/link";

import { CurrentEventCell, lookupEvents } from "@/components/records/current-event-cell";
import { FingerprintShort } from "@/components/records/fingerprint";
import { ReproducibilityBadge } from "@/components/state/reproducibility-badge";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import type { RecordSummary } from "@/lib/api/schema";
import { EPS_KIND_LABEL, PERIOD_CLASS_LABEL } from "@/lib/filings-view";
import { formatUtcInstant } from "@/lib/format";
import { gapSummary, shortId } from "@/lib/record-wording";
import { recordPath } from "@/lib/record-view";

const horizonsText = (horizons: readonly number[]) => `${horizons.map((value) => `+${value}`).join(", ")} sessions`;

const columns = (events: ReturnType<typeof lookupEvents>): DataTableColumn<RecordSummary>[] => [
  {
    id: "record",
    header: "Record",
    cell: (record) => (
      <Link href={recordPath(record.record_id)} className="type-id text-accent hover:underline">
        {shortId(record.record_id)}
        <span aria-hidden="true">…</span>
        <span className="sr-only">{record.record_id.slice(8)}</span>
      </Link>
    ),
  },
  {
    id: "saved",
    header: "Saved (UTC)",
    cell: (record) => <span className="num whitespace-nowrap">{formatUtcInstant(record.created_at)}</span>,
  },
  {
    id: "reproducibility",
    header: "Reproducibility",
    cell: (record) => (
      <span className="flex flex-col items-start gap-1">
        <ReproducibilityBadge level={record.reproducibility_level} />
        {record.reproducibility_gaps.length > 0 ? (
          <span className="type-meta">
            {record.reproducibility_gaps.length === 1 ? "Gap" : "Gaps"}: {gapSummary(record.reproducibility_gaps)}
          </span>
        ) : null}
      </span>
    ),
  },
  {
    id: "event",
    // The header says what this is: a lookup of the event as it is NOW, not stored in the baseline.
    header: "Event (current record)",
    cell: (record) => <CurrentEventCell lookup={events.get(record.request.event_id)!} />,
  },
  {
    id: "request",
    header: "Stored request",
    priority: "P2",
    cell: ({ request }) => (
      <span className="flex flex-col gap-0.5">
        <span className="num">{horizonsText(request.horizons)}</span>
        <span className="type-meta">
          Benchmark: {request.benchmark_id === null ? <span className="text-text-3">none</span> : <span className="type-id">{shortId(request.benchmark_id)}…</span>}
        </span>
        <span className="type-meta">
          Filing:{" "}
          {request.filing_accession === null ? (
            <span className="text-text-3">none</span>
          ) : (
            <>
              <span className="type-id">{request.filing_accession}</span>
              {request.eps_period_class && request.eps_kind ? ` · ${PERIOD_CLASS_LABEL[request.eps_period_class]} · ${EPS_KIND_LABEL[request.eps_kind]}` : null}
            </>
          )}
        </span>
      </span>
    ),
  },
  {
    id: "manifest",
    header: "Manifest fingerprint",
    priority: "P3",
    cell: (record) => <FingerprintShort value={record.manifest_fingerprint} />,
  },
  {
    id: "result",
    header: "Result fingerprint",
    priority: "P3",
    cell: (record) => <FingerprintShort value={record.result_fingerprint} />,
  },
];

/**
 * One page of saved baselines, in the API's order (newest saved first). Every column is a field the
 * list endpoint returns, except the event column, which is the CURRENT event record looked up for
 * readability (one read per distinct event on the page) and is labelled as such.
 */
export function RecordsTable({ records }: { readonly records: readonly RecordSummary[] }) {
  const events = lookupEvents(records.map((record) => record.request.event_id));
  return (
    <div className="flex flex-col gap-2">
      <DataTable caption="Saved research baselines" columns={columns(events)} rows={records} rowKey={(record) => record.record_id} />
      <p className="type-meta max-w-3xl">
        <strong className="font-medium text-text-2">Event (current record)</strong> is the event as it is stored now, looked up for readability. It is not
        part of a saved baseline, which keeps its own event facts. The two fingerprints are different identities: the manifest fingerprint identifies the
        request, methodology and consumed evidence; the result fingerprint identifies the stored result. Hover a shortened value for the whole, or open the record.
      </p>
    </div>
  );
}
