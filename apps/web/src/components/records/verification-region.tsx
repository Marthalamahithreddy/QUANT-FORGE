import Link from "next/link";

import { RecordFailure } from "@/components/records/record-failure";
import { VerificationResult } from "@/components/records/verification-result";
import { ErrorState } from "@/components/ui/error-state";
import { verifyRecord } from "@/lib/api/records";
import { recordHref, recordPath } from "@/lib/record-view";

/**
 * Runs the verification (ONE request) and renders its outcome. It is rendered inside its own keyed
 * Suspense boundary beneath the stored baseline, so a slow verification never holds the baseline
 * back, and it exists only when the reader asked for it (`?verify=1`).
 *
 * A verification STATUS (match, evidence incomplete, manifest changed, result changed) is a domain
 * answer and is rendered by `VerificationResult`. Everything else here is a failure of the
 * request, kept distinct and worded without any response text: 404, 409 (the record's event is no
 * longer stored, so there is nothing to re-run), 500 (the stored record failed integrity
 * validation), 503, network and contract failures.
 */
export async function VerificationRegion({ recordId }: { readonly recordId: string }) {
  const result = await verifyRecord(recordId);
  if (result.kind === "ok") return <VerificationResult report={result.data} />;

  if (result.kind === "conflict") {
    return (
      <ErrorState
        title="This record cannot be re-run"
        detail="The earnings event of this record is no longer stored, so there is nothing to re-run it against. The stored baseline above is unaffected."
        action={
          <Link href={recordPath(recordId)} className="text-accent hover:underline">
            Hide the verification
          </Link>
        }
      />
    );
  }
  return (
    <RecordFailure
      failure={result}
      subject="the verification of this saved record"
      retryHref={recordHref(recordId, true)}
      notFoundTitle="This record is no longer stored"
    >
      <p className="mt-1.5">The stored baseline above is unaffected: a failed verification is not a verification result and changes nothing.</p>
    </RecordFailure>
  );
}
