import Link from "next/link";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/ui/error-state";
import { failureCopy } from "@/lib/api/messages";
import type { ApiFailure } from "@/lib/api/result";

export interface ApiFailureStateProps {
  readonly failure: ApiFailure;
  /** What was being read, for the lead sentence ("the earnings events"). */
  readonly subject: string;
  /** A page-specific title for `not_found` ("No stored event has this identifier"). */
  readonly notFoundTitle?: string;
  /** Where "Try again" goes: the same URL. Omitted when a retry makes no sense. */
  readonly retryHref?: string;
  readonly children?: ReactNode;
}

/**
 * A failed READ (HTTP, network or contract), never a domain refusal: a REFUSED, MISSING or
 * AMBIGUOUS status arrives inside a successful response and is shown with `DomainState`.
 * The wording comes from one table (`failureCopy`), so no response text can reach a reader.
 */
export function ApiFailureState({ failure, subject, notFoundTitle, retryHref, children }: ApiFailureStateProps) {
  const copy = failureCopy(failure);
  return (
    <ErrorState
      title={failure.kind === "not_found" && notFoundTitle ? notFoundTitle : copy.title}
      detail={`The QuantForge API did not return ${subject}. ${copy.body}`}
      action={
        retryHref && failure.kind !== "not_found" && failure.kind !== "rejected" && failure.kind !== "contract" ? (
          <Link href={retryHref} className="text-accent hover:underline">
            Try again
          </Link>
        ) : undefined
      }
    >
      {children}
    </ErrorState>
  );
}
