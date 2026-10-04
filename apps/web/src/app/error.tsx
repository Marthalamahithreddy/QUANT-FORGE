"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";

/** Last-resort boundary: a render failure becomes a message, never a blank page. */
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
    <main id="main-content" className="mx-auto flex max-w-3xl flex-col gap-4 p-8">
      <ErrorState title="Something went wrong rendering this page">
        The page failed to render. This is a frontend error, not missing data.
      </ErrorState>
      <Button onClick={() => retry()} className="w-fit">
        Try again
      </Button>
    </main>
  );
}
