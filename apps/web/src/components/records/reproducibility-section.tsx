import { ReproducibilityBadge } from "@/components/state/reproducibility-badge";
import { Section } from "@/components/ui/section";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import { Callout } from "@/components/ui/callout";
import type { RecordDetail } from "@/lib/api/record-types";
import { BASIS_LABEL } from "@/lib/availability";
import { GAP_LABEL, GAP_SENTENCE } from "@/lib/record-wording";
import { REPRODUCIBILITY_META } from "@/lib/state";

/**
 * How completely the baseline's consumed evidence is pinned. COMPLETE and PARTIAL are statements
 * about the EVIDENCE TRAIL: PARTIAL does not mean the result is wrong, failed or unusable, and it is
 * therefore not shown as a warning banner or an error. The gaps are the backend's own list, each
 * with what it means; the level is the backend's and is never recomputed here.
 *
 * Residual assumptions are not part of the record contract (the API does not supply them for a
 * record), so none are listed here and none are invented.
 */
export function ReproducibilitySection({ record }: { readonly record: RecordDetail }) {
  const { methodology } = record.manifest;
  const level = record.reproducibility_level;
  return (
    <Section id="section-reproducibility" title="Reproducibility" note="Derived by the backend when the baseline was saved.">
      <div className="flex flex-col gap-3">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <ReproducibilityBadge level={level} />
          <span className="type-meta max-w-3xl">{REPRODUCIBILITY_META[level].sentence}</span>
        </p>

        {record.reproducibility_gaps.length === 0 ? (
          <Callout role="note" label="Reproducibility gaps">
            <p>No gaps: nothing the baseline consumed lacks a recorded ingestion run or pinned artifact.</p>
          </Callout>
        ) : (
          <div className="flex flex-col gap-1.5">
            <h3 className="text-xs font-medium tracking-wide text-text-2 uppercase">
              {record.reproducibility_gaps.length === 1 ? "Gap" : "Gaps"} ({record.reproducibility_gaps.length})
            </h3>
            <ul className="flex flex-col gap-2">
              {record.reproducibility_gaps.map((gap) => (
                <li key={gap} className="border-l-[3px] border-solid border-l-text-3 py-0.5 pl-3 text-sm">
                  <span className="font-medium text-text">{GAP_LABEL[gap]}</span> <span className="type-id text-text-3">{gap}</span>
                  <p className="type-meta">{GAP_SENTENCE[gap]}</p>
                </li>
              ))}
            </ul>
          </div>
        )}

        <details className="rounded border border-border-subtle">
          <summary className="cursor-pointer px-3 py-2 text-sm max-lg:min-h-11">
            Methodology identity <span className="type-meta">· {methodology.components.length} versioned components</span>
          </summary>
          <div className="flex flex-col gap-2 border-t border-border-subtle p-3">
            <KeyValueList>
              {methodology.components.map(([component, version]) => (
                <KeyValue key={component} label={component}>
                  <span className="type-id">{version}</span>
                </KeyValue>
              ))}
              <KeyValue label="Alignment window">
                <span className="num">{methodology.alignment_window_days}</span> days
              </KeyValue>
              <KeyValue label="Approved availability bases">
                {methodology.approved_availability_bases.length === 0 ? "None" : methodology.approved_availability_bases.map((basis) => BASIS_LABEL[basis]).join(", ")}
              </KeyValue>
            </KeyValueList>
            <p className="type-meta max-w-3xl">
              These are the explicit version labels of the computation that produced this baseline. They are not the manifest fingerprint, and they are not a version of the record.
            </p>
          </div>
        </details>
      </div>
    </Section>
  );
}
