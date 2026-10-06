import type { ReactNode } from "react";

/** A definition list laid out as label / value rows. */
export function KeyValueList({ children }: { readonly children: ReactNode }) {
  return <dl className="grid gap-x-6 gap-y-2.5 sm:grid-cols-[11rem_minmax(0,1fr)]">{children}</dl>;
}

/**
 * One label / value row. Values may hold numbers, so they use tabular figures; a missing or
 * non-value state is passed in as content (a `DomainState`), never as a blank.
 */
export function KeyValue({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <>
      <dt className="type-label">{label}</dt>
      <dd className="num min-w-0 text-sm break-words text-text">{children}</dd>
    </>
  );
}
