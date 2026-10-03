"use client";

import { useEffect } from "react";

import { StatePanel } from "@/components/ui/state-panel";

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
      <StatePanel kind="error" title="Something went wrong rendering this page">
        The page failed to render. This is a frontend error, not missing data.
      </StatePanel>
      <button
        type="button"
        onClick={() => retry()}
        className="w-fit rounded-md border border-border-strong bg-surface-3 px-3 py-1.5 text-sm text-text-primary hover:border-accent"
      >
        Try again
      </button>
    </main>
  );
}
