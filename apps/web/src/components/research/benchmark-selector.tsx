import { AppLink } from "@/components/ui/app-link";

import { SelectField } from "@/components/ui/fields";
import { Callout } from "@/components/ui/callout";
import { getBenchmarksOnce } from "@/lib/api/research";
import type { BenchmarkItem } from "@/lib/api/schema";
import { failureSummary } from "@/lib/api/messages";
import { BENCHMARK_PAGE_SIZE, researchHref, type ResearchConfigState } from "@/lib/research-view";

const label = (item: BenchmarkItem, collides: boolean): string =>
  `${item.name}${item.symbol ? ` (${item.symbol})` : ""}${item.currency ? ` · ${item.currency}` : ""}${collides ? ` · id ${item.id.slice(0, 8)}` : ""}${item.is_active ? "" : " · inactive"}`;

const shortId = (id: string) => `${id.slice(0, 8)}…`;

/**
 * Explicit benchmark selection.
 *
 * - The initial state is "None (stock returns only)". Nothing is chosen, recommended or defaulted,
 *   even if exactly one benchmark exists.
 * - The list is the backend's index, one page at a time (it is paginated: the first page is not the
 *   universe, and there is no search over a partial page). Active benchmarks come first, inactive
 *   ones after, labelled inactive (they stay selectable: a retired series is still a series).
 * - A selected id that is not on this page stays selected and is named by its id.
 * - If the index cannot be read, the study without a benchmark is unaffected: the selector says so,
 *   keeps any selected id, and offers to clear it.
 */
export async function BenchmarkSelector({ eventId, config }: { readonly eventId: string; readonly config: ResearchConfigState }) {
  const selected = config.query.benchmarkId ?? "";
  const offset = (config.benchmarkPage - 1) * BENCHMARK_PAGE_SIZE;
  const result = await getBenchmarksOnce(offset);
  const clearHref = researchHref(eventId, { ...config.query, benchmarkId: undefined });

  if (result.kind !== "ok") {
    return (
      <div className="flex min-w-64 flex-col gap-1">
        <span className="type-label">Benchmark</span>
        {selected ? <input type="hidden" name="benchmark_id" value={selected} /> : null}
        <p className="text-sm text-text">{selected ? <>Selected: <span className="type-id">{shortId(selected)}</span></> : "None (stock returns only)"}</p>
        <p className="type-meta max-w-sm" role="status">
          The benchmark list could not be read ({failureSummary(result)}) so none can be chosen now. The study without a benchmark is unaffected.
          {selected ? (
            <>
              {" "}
              <AppLink href={clearHref} className="text-accent hover:underline">
                Remove the selected benchmark
              </AppLink>
            </>
          ) : null}
        </p>
      </div>
    );
  }

  const { items, total, offset: at, limit } = result.data;
  const names = new Map<string, number>();
  for (const item of items) names.set(item.name, (names.get(item.name) ?? 0) + 1);
  const active = items.filter((item) => item.is_active);
  const inactive = items.filter((item) => !item.is_active);
  const onPage = items.find((item) => item.id === selected);
  const option = (item: BenchmarkItem) => (
    <option key={item.id} value={item.id}>
      {label(item, (names.get(item.name) ?? 0) > 1)}
    </option>
  );
  const hasPrevious = config.benchmarkPage > 1;
  const hasNext = at + items.length < total;

  return (
    <div className="flex min-w-64 flex-col gap-1.5">
      <SelectField id="benchmark_id" label="Benchmark" defaultValue={selected}>
        <option value="">None (stock returns only)</option>
        {selected !== "" && !onPage ? <option value={selected}>{`Selected benchmark (id ${shortId(selected)}), not on this page`}</option> : null}
        {active.map(option)}
        {inactive.length > 0 ? <optgroup label="Inactive (retired series)">{inactive.map(option)}</optgroup> : null}
      </SelectField>
      <p className="type-meta max-w-sm">Choose a benchmark to compute benchmark-relative and abnormal returns. Nothing is chosen for you.</p>
      {total === 0 ? <p className="type-meta">No benchmarks are stored.</p> : null}
      {total > limit || at > 0 ? (
        <p className="type-meta max-w-sm" data-testid="benchmark-paging">
          Showing benchmarks {at + 1}–{at + items.length} of {total} stored. This list is one page of the index, not a search.{" "}
          {hasPrevious ? (
            <AppLink href={researchHref(eventId, config.query, config.benchmarkPage - 1)} className="text-accent hover:underline">
              Previous benchmarks
            </AppLink>
          ) : null}
          {hasPrevious && hasNext ? " · " : null}
          {hasNext ? (
            <AppLink href={researchHref(eventId, config.query, config.benchmarkPage + 1)} className="text-accent hover:underline">
              Next benchmarks
            </AppLink>
          ) : null}
        </p>
      ) : null}
      {onPage && !onPage.is_active ? (
        <Callout role="note" label="Inactive benchmark" tone="warning">
          <p className="text-xs">
            This benchmark is marked inactive: no longer used for new research, retained for history. That does not mean its stored history is invalid.
          </p>
        </Callout>
      ) : null}
    </div>
  );
}
