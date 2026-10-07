import type { RecordDetail } from "@/lib/api/record-types";
import { BASELINE_SENTENCE } from "@/lib/record-wording";
import { formatUtcInstant } from "@/lib/format";

/**
 * The first thing on a saved record: this is a STORED BASELINE, historical persisted output. It is
 * deliberately not a result card: it never says "current", "live" or "latest", and it is the
 * counterpart of the live workspace, where nothing is called a baseline.
 */
export function BaselineBanner({ record }: { readonly record: RecordDetail }) {
  return (
    <div role="note" aria-label="Stored baseline" className="flex flex-col gap-1.5 rounded border border-border border-l-[3px] border-l-accent bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="rounded border border-solid border-accent/60 px-1.5 py-0.5 text-[11px] leading-4 font-semibold tracking-wide text-accent uppercase">
          Stored baseline
        </span>
        <span className="type-meta">Immutable record</span>
        <span className="type-meta">
          Saved <span className="num">{formatUtcInstant(record.created_at)}</span>
        </span>
      </div>
      <p className="type-meta max-w-3xl">{BASELINE_SENTENCE}</p>
    </div>
  );
}
