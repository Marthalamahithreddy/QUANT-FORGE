import type { ReactNode } from "react";

/** The approved container widths (spec section 6.1). Pages choose one; none invents its own. */
export type PageWidth = "reading" | "workspace" | "table";

const WIDTH: Record<PageWidth, string> = {
  /** 64rem: detail pages and prose. */
  reading: "max-w-reading",
  /** 96rem: the research workspace. */
  workspace: "max-w-workspace",
  /** 120rem: dense lists. */
  table: "max-w-table",
};

export interface PageContainerProps {
  readonly width?: PageWidth;
  readonly children: ReactNode;
}

export function PageContainer({ width = "reading", children }: PageContainerProps) {
  return <div className={`mx-auto flex w-full flex-col gap-5 ${WIDTH[width]}`}>{children}</div>;
}
