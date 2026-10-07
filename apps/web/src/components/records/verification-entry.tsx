import { Suspense } from "react";

import { VerificationRegion } from "@/components/records/verification-region";
import { FilterErrors } from "@/components/ui/filter-errors";
import { LinkButton } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { recordHref, type RecordViewState } from "@/lib/record-view";

function VerificationSkeleton() {
  return (
    <SkeletonRegion label="Verifying this baseline against the evidence stored now">
      <Skeleton width="14rem" height="0.75rem" />
      <Skeleton height="9rem" />
    </SkeletonRegion>
  );
}

/**
 * The verification section. By default it only EXPLAINS verification and offers it: nothing is run
 * until the reader follows the link (`?verify=1`), because a verification re-assembles the study
 * against today's stored evidence and is an act, not part of reading a baseline. When asked for,
 * it streams in beneath the baseline in its own boundary, keyed by the record and the request, so
 * the baseline above never waits for it.
 */
export function VerificationEntry({ recordId, state }: { readonly recordId: string; readonly state: RecordViewState }) {
  return (
    <Section id="verification" title="Verification" note="Optional. Compares a re-run with this baseline.">
      <div className="flex flex-col gap-3">
        <p className="type-meta max-w-3xl">
          Verification re-runs this record&rsquo;s own request against the evidence stored <em>now</em> and compares the outcome with the stored baseline.
        </p>
        {state.errors.length > 0 ? <FilterErrors id="verify-errors" errors={state.errors} title="Verification was not run" /> : null}
        {state.verify ? (
          <Suspense key={`${recordId}:verify`} fallback={<VerificationSkeleton />}>
            <VerificationRegion recordId={recordId} />
          </Suspense>
        ) : (
          <div>
            <LinkButton href={`${recordHref(recordId, true)}#verification`}>Verify against current evidence</LinkButton>
          </div>
        )}
      </div>
    </Section>
  );
}
