import Link from "next/link";

import { ChangedPaths } from "@/components/records/changed-paths";
import { ReproducibilityBadge } from "@/components/state/reproducibility-badge";
import { VerificationBadge } from "@/components/state/verification-badge";
import { Callout } from "@/components/ui/callout";
import { Frame } from "@/components/ui/frame";
import type { Verification, VerificationSide } from "@/lib/api/schema";
import { recordPath } from "@/lib/record-view";
import { GAP_LABEL, SECTION_LABEL, VERIFICATION_NOTE, VERIFICATION_SENTENCE } from "@/lib/record-wording";

const FINGERPRINTS = [
  ["request", "Request"],
  ["methodology", "Methodology"],
  ["evidence", "Evidence"],
  ["manifest", "Manifest"],
  ["result", "Result"],
] as const;

function Side({ title, side }: { readonly title: string; readonly side: VerificationSide }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <h3 className="flex flex-wrap items-center gap-x-2 text-xs font-medium tracking-wide text-text-2 uppercase">
        {title}
        <ReproducibilityBadge level={side.reproducibility_level} />
      </h3>
      {side.reproducibility_gaps.length > 0 ? (
        <p className="type-meta">Gaps: {side.reproducibility_gaps.map((gap) => GAP_LABEL[gap]).join(", ")}</p>
      ) : null}
      <dl className="flex flex-col gap-2">
        {FINGERPRINTS.map(([key, label]) => (
          <div key={key} className="min-w-0">
            <dt className="type-label">{label} fingerprint</dt>
            <dd>
              <code className="type-id break-all">{side.fingerprints[key]}</code>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/**
 * The verifier's report, rendered as returned. The status is the API's: it is never derived here
 * from the fingerprints, the paths or the levels, and the fingerprints of the two sides are shown
 * side by side without any sameness mark of our own. Changed paths come from the backend and are
 * grouped for reading only.
 *
 * Verification is a comparison: nothing is saved, replaced or refreshed, and the stored baseline
 * above is untouched whatever the status.
 */
export function VerificationResult({ report }: { readonly report: Verification }) {
  return (
    <Frame
      id="verification-result"
      title="Verification result"
      note={`Compared with ${report.baseline === "SUPPLIED" ? "this stored baseline" : "a fresh rerun"}.`}
      actions={<VerificationBadge status={report.status} />}
    >
      <div className="flex flex-col gap-4">
        <p className="max-w-3xl text-sm text-text">{VERIFICATION_SENTENCE[report.status]}</p>
        <p className="type-meta max-w-3xl">{VERIFICATION_NOTE}</p>

        {report.status === "MANIFEST_CHANGED" ? (
          <>
            <div className="flex flex-col gap-1">
              <h3 className="text-xs font-medium tracking-wide text-text-2 uppercase">Changed manifest sections</h3>
              <p className="text-sm">{report.changed_sections.map((section) => SECTION_LABEL[section]).join(", ")}</p>
            </div>
            <ChangedPaths
              title="Changed manifest paths"
              paths={report.changed_paths}
              explanation="Paths inside the manifest (request, methodology, evidence) that differ between the baseline and the rerun."
            />
          </>
        ) : null}

        {report.status === "MANIFEST_CHANGED" || report.status === "RESULT_CHANGED" ? (
          <ChangedPaths
            title="Changed result paths"
            paths={report.changed_result_paths}
            explanation="Paths inside the result that differ. The values are not shown here; this page does not compare financial values."
          />
        ) : null}

        {report.status === "EVIDENCE_INCOMPLETE" ? (
          <Callout role="note" label="Incomplete evidence" tone="warning">
            <p>
              The baseline, the rerun or both have gaps in their evidence provenance (the levels and gaps are listed below). Nothing about the result changed.
            </p>
          </Callout>
        ) : null}

        <div className="grid gap-6 md:grid-cols-2">
          <Side title="Stored baseline" side={report.expected} />
          <Side title="Rerun now" side={report.actual} />
        </div>

        <p className="text-sm">
          <Link href={recordPath(report.record_id)} className="text-accent hover:underline">
            Hide the verification
          </Link>
        </p>
      </div>
    </Frame>
  );
}
