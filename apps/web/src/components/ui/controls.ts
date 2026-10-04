/**
 * Shared class strings for native form controls.
 *
 * The boundary is `border-control` (>= 3:1 against the surfaces it sits on), not `border`, which
 * is a decorative hairline and fails WCAG 1.4.11 for a control. Controls grow to 44px below the
 * desktop breakpoint (touch target policy).
 */

export const CONTROL =
  "rounded-md border border-border-control bg-surface-elevated px-2.5 py-1.5 text-sm text-text placeholder:text-text-3 max-lg:min-h-11";

export const BUTTON_BASE =
  "inline-flex items-center justify-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium max-lg:min-h-11";

export const BUTTON_VARIANT = {
  secondary:
    "border-border-control bg-surface-raised text-text hover:border-accent hover:bg-hover",
  primary: "border-accent bg-accent text-accent-contrast hover:opacity-90",
  quiet: "border-transparent text-accent hover:underline",
} as const;

export type ButtonVariant = keyof typeof BUTTON_VARIANT;
