import { Children, isValidElement, type ReactNode } from "react";

import { StateGlyph } from "@/components/state/glyphs";
import { redactUrls } from "@/lib/safe-text";

/** Redacts addresses in string children (a caller may interpolate a message into JSX). */
function redactNode(node: ReactNode): ReactNode {
  if (typeof node === "string") return redactUrls(node);
  if (Array.isArray(node)) {
    return Children.map(node, (child) => (isValidElement(child) ? child : redactNode(child)));
  }
  return node;
}

export interface ErrorStateProps {
  /** Lets a form control point at this explanation (`aria-describedby`). */
  readonly id?: string;
  readonly title: string;
  /** A short, already-safe description. Any address in it is redacted before it is shown. */
  readonly detail?: string;
  readonly children?: ReactNode;
  readonly action?: ReactNode;
}

/**
 * An APPLICATION or transport failure (HTTP error, unreachable service, contract drift).
 * Domain refusals are not errors and use `RefusalNotice` instead.
 *
 * Safe by construction: it never prints a service address (URLs in `detail` are redacted), a
 * driver or SQL message, a stack or a DSN. Callers pass `failureCopy(...)` text (api/messages.ts), never a raw error message.
 */
export function ErrorState({ id, title, detail, children, action }: ErrorStateProps) {
  return (
    <div
      id={id}
      role="alert"
      className="rounded border border-danger/60 border-l-[3px] border-l-danger bg-surface p-4"
    >
      <p className="flex items-center gap-2 text-sm font-medium text-text">
        <span className="text-danger">
          <StateGlyph kind="ERROR" />
        </span>
        {title}
      </p>
      {detail || children ? (
        <div className="mt-1.5 max-w-3xl text-sm leading-relaxed text-text-2">
          {detail ? <p>{redactUrls(detail)}</p> : null}
          {redactNode(children)}
        </div>
      ) : null}
      {action ? <div className="mt-3 text-sm">{action}</div> : null}
    </div>
  );
}
