import { MoneyValue } from "@/components/companies/figure";
import { FinancialValue } from "@/components/financial/financial-value";
import type { MetricSection } from "@/lib/api/schema";
import { barPercents } from "@/lib/bar-widths";
import { METRIC_KIND } from "@/lib/companies-view";

/**
 * This period against the same period a year earlier, as two bars of one scale. Bar length is the
 * MAGNITUDE of the exact figure (whole percent of the larger one, integer arithmetic only); the
 * labels are the exact values, so a loss reads as a negative number with a bar of its size.
 * Drawn only when both figures are AVAILABLE; otherwise the card says why instead.
 */
export function ComparisonBars({ section, periodLabel }: { readonly section: MetricSection; readonly periodLabel: string }) {
  const current = section.current.fact?.value ?? null;
  const prior = section.prior.fact?.value ?? null;
  if (section.current.status !== "AVAILABLE" || section.prior.status !== "AVAILABLE" || current === null || prior === null) return null;
  const [currentWidth, priorWidth] = barPercents([current, prior]);
  if (currentWidth === null || priorWidth === null) return null;
  const rows = [
    { key: "current", label: periodLabel, exact: current, width: currentWidth, tone: "bg-accent" },
    { key: "prior", label: "Same period, prior year", exact: prior, width: priorWidth, tone: "bg-text-3" },
  ] as const;
  return (
    <ul className="flex flex-col gap-2" aria-label="Current and prior-year figure">
      {rows.map((row) => (
        <li key={row.key} className="flex flex-col gap-0.5">
          <div className="flex items-baseline justify-between gap-2 text-xs text-text-2">
            <span>{row.label}</span>
            <span className="text-sm text-text">
              {METRIC_KIND[section.metric] === "usd" ? <MoneyValue exact={row.exact} /> : <FinancialValue value={row.exact} kind="eps" />}
            </span>
          </div>
          <div className="h-2 w-full rounded-sm bg-border-subtle" aria-hidden="true">
            <div className={`h-2 rounded-sm ${row.tone}`} style={{ width: `${row.width}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}
