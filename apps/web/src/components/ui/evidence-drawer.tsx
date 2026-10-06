"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

export interface EvidenceDrawerProps {
  /** What the evidence is FOR; becomes the trigger's accessible name ("Evidence for the +5 session stock return"). */
  readonly subject: string;
  /** The drawer's heading. */
  readonly title: string;
  /** Server-rendered evidence. It arrives as children, so the drawer performs no fetch. */
  readonly children: ReactNode;
}

/**
 * An evidence trigger and the drawer it opens: a native modal `<dialog>` used as a right-hand panel
 * (a full-height sheet on narrow screens). `showModal()` traps focus, makes the page inert and
 * closes on Esc; closing returns focus to the trigger. A Client Component only for that browser
 * behaviour: the evidence itself is server-rendered content, mounted only while the drawer is open.
 */
export function EvidenceDrawer({ subject, title, children }: EvidenceDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const headingId = useId();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  function show() {
    setOpen(true);
    dialogRef.current?.showModal();
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="dialog"
        onClick={show}
        className="text-xs text-accent underline-offset-2 hover:underline max-lg:inline-flex max-lg:min-h-11 max-lg:items-center"
      >
        Evidence <span className="sr-only">for {subject}</span>
      </button>
      <dialog
        ref={dialogRef}
        aria-labelledby={headingId}
        onClick={(event) => {
          if (event.target === event.currentTarget) dialogRef.current?.close();
        }}
        onClose={() => {
          setOpen(false);
          triggerRef.current?.focus();
        }}
        className="qf-evidence fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-full max-w-none border-0 border-l border-border bg-surface p-0 text-text md:w-[28rem]"
      >
        {open ? (
          <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
            <div className="flex items-start justify-between gap-3">
              <h2 id={headingId} className="type-section pt-3">
                {title}
              </h2>
              <button
                ref={closeRef}
                type="button"
                onClick={() => dialogRef.current?.close()}
                className="inline-flex size-11 shrink-0 items-center justify-center rounded-md border border-border-control text-text hover:bg-hover"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false">
                  <path d="M3 3l8 8M11 3l-8 8" />
                </svg>
                <span className="sr-only">Close evidence</span>
              </button>
            </div>
            {children}
          </div>
        ) : null}
      </dialog>
    </>
  );
}
