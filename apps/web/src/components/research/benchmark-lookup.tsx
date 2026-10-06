import type { ReactNode } from "react";

import { getBenchmarksOnce } from "@/lib/api/research";
import type { BenchmarkItem } from "@/lib/api/schema";
import { BENCHMARK_PAGE_SIZE } from "@/lib/research-view";

/** `Name (SYMBOL)` plus an inactive tag: how a benchmark is named wherever its name is known. */
export const benchmarkName = (item: BenchmarkItem): string =>
  `${item.name}${item.symbol ? ` (${item.symbol})` : ""}${item.is_active ? "" : " · inactive"}`;

/**
 * Looks a benchmark up in the index page the selector is showing, and renders `children` with it
 * (or `undefined` when it is not on that page, or the index could not be read).
 *
 * The lookup only NAMES a benchmark the reader already selected. It lives in its own `Suspense`
 * boundary wherever it is used, so a slow or failed index read never holds back the study, and the
 * index page is shared per request with the selector.
 */
export async function BenchmarkLookup({
  id,
  page,
  children,
}: {
  readonly id: string;
  readonly page: number;
  readonly children: (item: BenchmarkItem | undefined) => ReactNode;
}) {
  const result = await getBenchmarksOnce((page - 1) * BENCHMARK_PAGE_SIZE);
  return <>{children(result.kind === "ok" ? result.data.items.find((item) => item.id === id) : undefined)}</>;
}
