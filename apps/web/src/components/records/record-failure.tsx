import type { ReactNode } from "react";

import { ApiFailureState } from "@/components/ui/api-failure";
import type { ApiFailure } from "@/lib/api/result";

export interface RecordFailureProps {
  readonly failure: ApiFailure;
  /** What was being read ("the saved research list", "this saved record"). */
  readonly subject: string;
  readonly retryHref?: string;
  readonly notFoundTitle?: string;
  readonly children?: ReactNode;
}

/**
 * A failed saved-research read. The shared failure wording applies, plus one sentence for the
 * status the record endpoints document specifically: HTTP 500 means a stored record failed the
 * backend's own integrity validation. That sentence is fixed; no response text, corruption detail
 * or identifier is ever shown. It is not "not found" and not a verification result.
 */
export function RecordFailure({ failure, subject, retryHref, notFoundTitle, children }: RecordFailureProps) {
  const integrity = failure.kind === "server_error" && failure.status === 500;
  return (
    <ApiFailureState failure={failure} subject={subject} retryHref={retryHref} notFoundTitle={notFoundTitle}>
      {integrity ? (
        <p className="mt-1.5">
          For saved records the API uses this response when a stored record fails its own integrity validation. Nothing from the record is shown, and the failure
          is not a verification result.
        </p>
      ) : null}
      {children}
    </ApiFailureState>
  );
}
