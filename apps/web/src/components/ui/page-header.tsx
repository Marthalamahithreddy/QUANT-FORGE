import type { ReactNode } from "react";

export interface PageHeaderProps {
  readonly eyebrow?: string;
  readonly title: string;
  readonly description?: ReactNode;
  readonly actions?: ReactNode;
}

/** One `h1` per page. The eyebrow is the section label; actions sit at the right. */
export function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border-subtle pb-4">
      <div className="flex min-w-0 flex-col gap-1.5">
        {eyebrow ? <p className="type-section">{eyebrow}</p> : null}
        <h1 className="type-title text-text">{title}</h1>
        {description ? <p className="type-body max-w-3xl text-text-2">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
