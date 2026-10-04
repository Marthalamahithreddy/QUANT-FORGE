"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";

/**
 * The collapsed navigation below the desktop breakpoint: a menu button and a modal drawer.
 *
 * A Client Component because it opens and closes a native `<dialog>` (browser focus handling).
 * `showModal()` traps focus, makes the page inert behind it, closes on Esc and returns focus to
 * the button. The navigation itself arrives as server-rendered `children`, mounted only while
 * open so a closed drawer adds nothing to the page or the accessibility tree.
 */
export function MobileNav({ children }: { readonly children: ReactNode }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  // Navigating closes the drawer.
  useEffect(() => {
    dialogRef.current?.close();
  }, [pathname]);

  // A drawer left open while the viewport grows to the desktop layout would strand a modal beside
  // the rail, so it closes at the breakpoint.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const desktop = window.matchMedia("(min-width: 1024px)");
    const onChange = () => {
      if (desktop.matches) dialogRef.current?.close();
    };
    desktop.addEventListener("change", onChange);
    return () => desktop.removeEventListener("change", onChange);
  }, []);

  // The contents mount just after showModal(), so the browser has nothing to focus yet: move focus
  // to the first control explicitly. Closing returns it to the menu button (platform behaviour).
  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  function show() {
    setOpen(true);
    dialogRef.current?.showModal();
  }

  function close() {
    dialogRef.current?.close();
  }

  function onDialogClick(event: MouseEvent<HTMLDialogElement>) {
    const target = event.target as HTMLElement;
    // A click on the backdrop (the dialog itself) or on any link inside dismisses it.
    if (target === event.currentTarget || target.closest("a") !== null) {
      close();
    }
  }

  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={show}
        className="inline-flex size-11 shrink-0 items-center justify-center rounded-md border border-border-control text-text hover:bg-hover lg:hidden"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false">
          <path d="M2 4h12M2 8h12M2 12h12" />
        </svg>
        <span className="sr-only">Open navigation</span>
      </button>
      <dialog
        ref={dialogRef}
        aria-label="Navigation"
        onClick={onDialogClick}
        onClose={() => setOpen(false)}
        className="qf-drawer fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-[min(18rem,85vw)] max-w-none border-0 border-r border-border bg-surface p-0 text-text"
      >
        {open ? (
          <div className="flex h-full flex-col gap-4 overflow-y-auto p-3">
            <div className="flex items-center justify-between">
              <span className="type-section px-2">Navigation</span>
              <button
                ref={closeRef}
                type="button"
                onClick={close}
                className="inline-flex size-11 items-center justify-center rounded-md border border-border-control text-text hover:bg-hover"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false">
                  <path d="M3 3l8 8M11 3l-8 8" />
                </svg>
                <span className="sr-only">Close navigation</span>
              </button>
            </div>
            <nav aria-label="Primary menu">{children}</nav>
          </div>
        ) : null}
      </dialog>
    </>
  );
}
