import { Figure } from "@/components/companies/figure";
import type { FilingFinancials, MetricId } from "@/lib/api/schema";
import { METRIC_IDS } from "@/lib/api/schema";
import { barPercents } from "@/lib/bar-widths";
import { METRIC_LABEL, METRIC_UNIT, quarterLabel } from "@/lib/companies-view";

interface Bar {
  readonly key: string;
  readonly label: string;
  readonly filing: FilingFinancials;
  readonly metric: MetricId;
}

/**
 * The stored quarters, oldest first, one bar per quarter whose figure is AVAILABLE. A quarter whose
 * figure is refused or missing is not drawn (and not zero): it is listed beneath as unavailable.
 * Only 10-Q periods are plotted; a fiscal Q4 lives in the 10-K and is never derived by subtraction.
 */
function quarterBars(filings: readonly FilingFinancials[], metric: MetricId): { bars: Bar[]; skipped: Bar[] } {
  const quarters = filings
    .filter((filing) => filing.period_class === "QUARTER")
    .slice()
    .sort(
      (a, b) =>
        (a.period_end ?? "").localeCompare(b.period_end ?? "") ||
        a.filing_date.localeCompare(b.filing_date) ||
        a.accession.localeCompare(b.accession),
    );
  const all = quarters.map((filing) => ({ key: `${filing.accession}-${metric}`, label: quarterLabel(filing), filing, metric }));
  const available = (bar: Bar) => bar.filing.metrics.find((m) => m.metric === metric)?.current.status === "AVAILABLE";
  return { bars: all.filter(available), skipped: all.filter((bar) => !available(bar)) };
}

export function QuarterlyTrend({ filings }: { readonly filings: readonly FilingFinancials[] }) {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {METRIC_IDS.map((metric) => {
        const { bars, skipped } = quarterBars(filings, metric);
        const values = bars.map((bar) => bar.filing.metrics.find((m) => m.metric === metric)?.current.fact?.value ?? "0");
        const widths = barPercents(values);
        return (
          <section key={metric} aria-label={`${METRIC_LABEL[metric]} by quarter`} className="flex flex-col gap-2 rounded border border-border bg-surface p-4">
            <h3 className="type-section">{METRIC_LABEL[metric]}</h3>
            <p className="type-meta">{METRIC_UNIT[metric]}, by stored quarter</p>
            {bars.length === 0 ? (
              <p className="type-meta">No quarter has an available figure.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {bars.map((bar, index) => {
                  const section = bar.filing.metrics.find((m) => m.metric === metric);
                  return (
                    <li key={bar.key} className="flex flex-col gap-0.5">
                      <div className="flex items-baseline justify-between gap-2 text-xs text-text-2">
                        <span>
                          {bar.label}
                          <span className="type-meta"> · ended {bar.filing.period_end}</span>
                        </span>
                        <span className="text-sm text-text">{section ? <Figure point={section.current} metric={metric} /> : null}</span>
                      </div>
                      <div className="h-2 w-full rounded-sm bg-border-subtle" aria-hidden="true">
                        <div className="h-2 rounded-sm bg-accent" style={{ width: `${widths[index] ?? "0"}%` }} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
            {skipped.length > 0 ? (
              <p className="type-meta">Not drawn (figure unavailable): {skipped.map((bar) => bar.label).join(", ")}.</p>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}
