import { BaselineBanner } from "@/components/records/baseline-banner";
import { RecordIdentity } from "@/components/records/record-identity";
import { ReproducibilitySection } from "@/components/records/reproducibility-section";
import { RerunSection } from "@/components/records/rerun-section";
import { SavedEvidence } from "@/components/records/saved-evidence";
import { StoredRequest } from "@/components/records/stored-request";
import { StoredResultView } from "@/components/records/stored-result-view";
import { VerificationEntry } from "@/components/records/verification-entry";
import type { RecordDetail } from "@/lib/api/record-types";
import type { RecordViewState } from "@/lib/record-view";

/**
 * A saved record, top to bottom: the stored baseline (identity, request, reproducibility, result,
 * evidence), then the two things that are NOT the baseline: an optional verification, and a link
 * to a new live study. The baseline sections need only the one record read and render without
 * waiting for either.
 */
export function RecordDetailView({ record, state }: { readonly record: RecordDetail; readonly state: RecordViewState }) {
  return (
    <>
      <BaselineBanner record={record} />
      <RecordIdentity record={record} />
      <StoredRequest record={record} />
      <ReproducibilitySection record={record} />
      <StoredResultView result={record.result} />
      <SavedEvidence evidence={record.manifest.evidence} />
      <VerificationEntry recordId={record.record_id} state={state} />
      <RerunSection record={record} />
    </>
  );
}
