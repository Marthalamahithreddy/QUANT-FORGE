"use client";

import { useEffect, useRef, useState } from "react";

export interface CopyValueProps {
  /** The exact text copied: never shortened or reformatted. */
  readonly value: string;
  /** What is copied, for the accessible name ("manifest fingerprint"). */
  readonly label: string;
}

type CopyStatus = "idle" | "copied" | "failed";

const FEEDBACK: Record<CopyStatus, string> = {
  idle: "",
  copied: "Copied",
  failed: "Could not copy. Select the value instead.",
};

/**
 * A copy action for one exact value. A Client Component only for the clipboard: the value itself
 * is rendered by the server beside it, so a failed copy (no clipboard permission, an insecure
 * context) changes nothing but its own message, and the page never depends on it.
 */
export function CopyValue({ value, label }: CopyValueProps) {
  const [status, setStatus] = useState<CopyStatus>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  async function copy() {
    let next: CopyStatus = "copied";
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      next = "failed";
    }
    setStatus(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setStatus("idle"), 4000);
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-x-2">
      <button
        type="button"
        onClick={copy}
        className="inline-flex items-center rounded-md border border-border-control px-2 py-0.5 text-xs text-text hover:bg-hover max-lg:min-h-11"
      >
        Copy{" "}
        <span className="sr-only">{label}</span>
      </button>
      <span role="status" aria-live="polite" className="type-meta">
        {FEEDBACK[status]}
      </span>
    </span>
  );
}
