import { ComparisonBars } from "@/components/companies/comparison-bars";
import { ChangeFigure, Figure } from "@/components/companies/figure";
import type { FilingFinancials } from "@/lib/api/schema";
import { METRIC_LABEL, METRIC_UNIT, fiscalLabel } from "@/lib/companies-view";

/** The most recent stored filing: one card per metric with the bars, the exact change and its ratio. */
export function MetricCards({ filing }: { readonly filing: FilingFinancials }) {
  const label = fiscalLabel(filing);
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {filing.metrics.map((section) => (
        <article
          key={section.metric}
          aria-label={METRIC_LABEL[section.metric]}
          className="flex flex-col gap-3 rounded border border-border bg-surface p-4"
        >
          <header className="flex flex-col gap-0.5">
            <h3 className="type-section">{METRIC_LABEL[section.metric]}</h3>
            <p className="type-meta">{METRIC_UNIT[section.metric]}</p>
          </header>
          <p className="text-2xl font-semibold text-text">
            <Figure point={section.current} metric={section.metric} />
          </p>
          <ComparisonBars section={section} periodLabel={label} />
          {section.prior.status !== "AVAILABLE" ? (
            <p className="type-meta">
              Prior-year figure: <Figure point={section.prior} metric={section.metric} />
            </p>
          ) : null}
          <p className="text-sm text-text-2">
            Change vs prior year: <ChangeFigure section={section} />
          </p>
        </article>
      ))}
    </div>
  );
}
