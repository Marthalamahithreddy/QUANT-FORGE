import Link from "next/link";
import { Suspense } from "react";

import type { EarningsEvent } from "@/lib/api/schema";
import { BenchmarkLookup, benchmarkName } from "@/components/research/benchmark-lookup";
import { TIMING_LABEL } from "@/lib/availability";
import { fiscalPeriod, tickersAtEvent } from "@/lib/earnings-view";
import { researchHref, type ResearchConfigState } from "@/lib/research-view";

const CHIP = "inline-flex items-center gap-1.5 rounded border border-border bg-surface-elevated px-2 py-0.5 text-xs text-text";

function Item({ label, children }: { readonly label: string; readonly children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="type-label">{label}</dt>
      <dd className="text-sm text-text">{children}</dd>
    </div>
  );
}

/** The selected benchmark by NAME when the index page that holds it was read, otherwise by id. */
function BenchmarkChip({ eventId, config }: { readonly eventId: string; readonly config: ResearchConfigState }) {
  const id = config.query.benchmarkId;
  if (!id) return <span className="text-text-2">none (stock returns only)</span>;
  const idLabel = <span className="type-id">{id.slice(0, 8)}…</span>;
  return (
    <span className={CHIP}>
      <Suspense fallback={idLabel}>
        <BenchmarkLookup id={id} page={config.benchmarkPage}>
          {(item) => (item ? benchmarkName(item) : idLabel)}
        </BenchmarkLookup>
      </Suspense>
      <Link href={researchHref(eventId, { ...config.query, benchmarkId: undefined })} aria-label="Remove the benchmark" className="text-accent hover:underline max-lg:flex max-lg:min-h-11 max-lg:items-center">
        remove
      </Link>
    </span>
  );
}

/**
 * Who and what this study is about, and its configuration in one place. The identity comes from the
 * stored event record only (company, fiscal period, event date, timing, tickers the backend
 * proved for that date); the configuration is read back from the URL. Nothing is inferred.
 */
export function ContextBar({ eventId, record, config }: { readonly eventId: string; readonly record: EarningsEvent; readonly config: ResearchConfigState }) {
  const tickers = tickersAtEvent(record);
  const invalid = config.errors.length > 0;
  return (
    <section aria-label="Research context" className="flex flex-col gap-2 rounded border border-border-subtle bg-surface px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
      <dl className="flex flex-wrap gap-x-6 gap-y-1">
        <Item label="Company">{record.company.display_name}</Item>
        <Item label="Ticker on the event date">
          {tickers.length > 0 ? <span className="type-id">{tickers.join(", ")}</span> : <span className="text-text-3">Not resolved</span>}
        </Item>
        <Item label="Event">
          <span className="num">
            {record.event_date} · {fiscalPeriod(record)} · {record.timing === "UNKNOWN" ? "timing not stated" : TIMING_LABEL[record.timing]}
          </span>
        </Item>
      </dl>
      <Link href={`/earnings/${eventId}`} className="text-sm text-accent hover:underline max-lg:flex max-lg:min-h-11 max-lg:items-center">
        Stored event record
      </Link>
      </div>
      <dl className="flex flex-wrap gap-x-6 gap-y-1">
        <Item label="Price basis">Split-adjusted (the only basis research reads)</Item>
        {invalid ? (
          // The address carries a configuration that cannot be used, so it is shown as typed and as NOT applied:
          // nothing is read back as "none" when the address says otherwise.
          <>
            <Item label="Horizons">
              <span className="num">{config.shown.horizons || "default"}</span>
              <span className="text-text-3"> · not applied</span>
            </Item>
            <Item label="Benchmark">
              <span className="type-id">{config.shown.benchmark_id || "none"}</span>
              <span className="text-text-3"> · not applied</span>
            </Item>
            <Item label="Filing">
              <span className="type-id">{config.shown.filing_accession || "none"}</span>
              <span className="text-text-3"> · not applied</span>
            </Item>
          </>
        ) : (
          <>
        <Item label="Horizons">
          <span className="num">{config.horizons.join(", ")}</span>
          {config.horizonsAreDefault ? <span className="text-text-3"> · default</span> : null}
        </Item>
        <Item label="Benchmark">
          <BenchmarkChip eventId={eventId} config={config} />
        </Item>
        <Item label="Filing">
          {config.query.filingAccession ? (
            <span className={CHIP}>
              <span className="type-id">{config.query.filingAccession}</span>
              <span className="text-text-3">
                {config.query.epsPeriodClass?.toLowerCase()} · {config.query.epsKind?.toLowerCase().replaceAll("_", " ")}
              </span>
              <Link
                href={researchHref(eventId, { ...config.query, filingAccession: undefined, epsPeriodClass: undefined, epsKind: undefined })}
                aria-label="Remove the filing"
                className="text-accent hover:underline max-lg:flex max-lg:min-h-11 max-lg:items-center"
              >
                remove
              </Link>
            </span>
          ) : (
            <span className="text-text-2">none (not linked)</span>
          )}
        </Item>
          </>
        )}
      </dl>
    </section>
  );
}
