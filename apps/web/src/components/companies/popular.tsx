import { Figure } from "@/components/companies/figure";
import { DomainState } from "@/components/state/domain-state";
import { AppLink } from "@/components/ui/app-link";
import { getCompanyFinancials } from "@/lib/api/companies";
import type { ApiResult } from "@/lib/api/result";
import type { CompanyFinancials, FilingFinancials } from "@/lib/api/schema";
import { METRIC_LABEL, METRIC_UNIT, companyHref, coverageOf, fiscalLabel, type Coverage } from "@/lib/companies-view";
import { POPULAR_COMPANIES, type PopularCompany } from "@/lib/popular-companies";

const COVERAGE_LABEL: Record<Coverage, string> = {
  FULL: "Full coverage",
  LIMITED: "Limited coverage",
  NONE: "No financial data",
};

/** Solid, dashed and dotted outlines repeat the text label; the label alone carries the meaning. */
const COVERAGE_STYLE: Record<Coverage, string> = {
  FULL: "border-solid border-border text-text",
  LIMITED: "border-dashed border-border text-text-2",
  NONE: "border-dotted border-border text-text-2",
};

function coveredPeriods(filings: readonly FilingFinancials[]): string {
  const quarters = filings.filter((filing) => filing.period_class === "QUARTER").length;
  const years = filings.length - quarters;
  const byEnd = [...filings].sort((a, b) => (a.period_end ?? "").localeCompare(b.period_end ?? ""));
  const oldest = byEnd[0];
  const newest = byEnd[byEnd.length - 1];
  const count = `${filings.length} ${filings.length === 1 ? "filing" : "filings"} (${quarters} quarterly, ${years} annual)`;
  return oldest !== undefined && newest !== undefined && oldest !== newest
    ? `${count}, ${fiscalLabel(oldest)} to ${fiscalLabel(newest)}`
    : count;
}

function Unavailable({ entry, kind, reason }: { readonly entry: PopularCompany; readonly kind: "MISSING" | "UNAVAILABLE"; readonly reason: string }) {
  return (
    <article aria-label={entry.ticker} data-coverage="UNAVAILABLE" className="flex flex-col gap-2 rounded border border-dotted border-border bg-surface p-4">
      <h3 className="type-section">
        <span className="type-id">{entry.ticker}</span>
      </h3>
      <p className="type-meta">CIK {entry.cik}</p>
      <p>
        <DomainState kind={kind} />
      </p>
      <p className="type-meta">{reason}</p>
    </article>
  );
}

function StoredCard({ data }: { readonly data: CompanyFinancials }) {
  const { company, filings } = data;
  const newest = filings[0];
  const { coverage, unavailable } = coverageOf(newest);
  return (
    <article aria-label={company.name} data-coverage={coverage} className="flex flex-col gap-3 rounded border border-border bg-surface p-4">
      <header className="flex flex-col gap-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h3 className="type-section">
            <AppLink href={companyHref(company.cik)} className="text-accent underline-offset-2 hover:underline">
              {company.name}
            </AppLink>
          </h3>
          <span className={`rounded border px-1.5 py-0.5 text-[11px] leading-4 font-medium whitespace-nowrap ${COVERAGE_STYLE[coverage]}`}>
            {COVERAGE_LABEL[coverage]}
          </span>
        </div>
        <p className="type-meta">
          {company.tickers.length > 0 ? `${company.tickers.join(", ")} · ` : ""}CIK {company.cik}
        </p>
      </header>
      {newest === undefined ? (
        <p className="type-meta">No SEC financial facts are stored for this company, so no figure is shown.</p>
      ) : (
        <>
          <p className="type-meta">
            Newest stored filing: {fiscalLabel(newest)} · {newest.form} · period ended {newest.period_end ?? "not stated"}
          </p>
          <dl className="flex flex-col gap-1.5 text-sm">
            {newest.metrics.map((section) => (
              <div key={section.metric} className="flex flex-wrap items-baseline justify-between gap-x-3">
                <dt className="text-text-2">
                  {METRIC_LABEL[section.metric]}
                  <span className="type-meta"> · {METRIC_UNIT[section.metric]}</span>
                </dt>
                <dd className="text-text">
                  <Figure point={section.current} metric={section.metric} />
                </dd>
              </div>
            ))}
          </dl>
          {coverage === "LIMITED" ? (
            <p className="type-meta">
              Not shown for the newest filing: {unavailable.map((metric) => METRIC_LABEL[metric]).join(", ")}. Each is named above with its reason, never
              zero.
            </p>
          ) : null}
          <footer className="flex flex-col gap-0.5">
            <p className="type-meta">{coveredPeriods(filings)}</p>
            <p className="type-meta">
              Evidence:{" "}
              <AppLink href={`${companyHref(company.cik)}#evidence-${newest.accession}`} className="type-id text-accent underline-offset-2 hover:underline">
                {newest.accession}
              </AppLink>
            </p>
          </footer>
        </>
      )}
    </article>
  );
}

function card(entry: PopularCompany, result: ApiResult<CompanyFinancials>) {
  if (result.kind === "ok") return <StoredCard key={entry.cik} data={result.data} />;
  if (result.kind === "not_found") {
    return <Unavailable key={entry.cik} entry={entry} kind="MISSING" reason="Not stored in this workspace: no company is stored under this CIK." />;
  }
  return <Unavailable key={entry.cik} entry={entry} kind="UNAVAILABLE" reason="This company's data could not be read from the API just now." />;
}

/**
 * The curated companies, each read from the QuantForge API (one financials request per entry; no
 * provider and no SEC request). No figure, name or period is written here: whatever the API returns
 * is shown, and whatever it does not return is shown as unavailable.
 */
export async function PopularCompanies() {
  const results = await Promise.all(POPULAR_COMPANIES.map((entry) => getCompanyFinancials(entry.cik)));
  const stored = results.filter((result) => result.kind === "ok").length;
  return (
    <>
      {stored === 0 ? (
        <p className="type-meta">None of the featured companies has stored financial data in this workspace.</p>
      ) : null}
      <div className="grid gap-3 md:grid-cols-2">{POPULAR_COMPANIES.map((entry, index) => card(entry, results[index]!))}</div>
    </>
  );
}
