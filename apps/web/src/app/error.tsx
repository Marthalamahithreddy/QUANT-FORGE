"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { PageContainer } from "@/components/ui/page-container";

/**
 * Last-resort boundary INSIDE the shell: a render failure becomes a message in the page region,
 * never a blank page, and the navigation stays usable.
 */
export default function RouteError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <PageContainer>
      <ErrorState page title="Something went wrong rendering this page">
        The page failed to render. This is a frontend error, not missing data.
        {error.digest ? <span className="type-id block pt-1 text-text-3">Reference {error.digest}</span> : null}
      </ErrorState>
      <Button onClick={() => retry()} className="w-fit">
        Try again
      </Button>
    </PageContainer>
  );
}
