import { redirect } from "next/navigation";
import { Suspense } from "react";

import { CompaniesResults } from "@/components/companies/companies-results";
import { PopularCompanies } from "@/components/companies/popular";
import { PopularSkeleton } from "@/components/companies/popular-skeleton";
import { FilterErrors } from "@/components/ui/filter-errors";
import { TextField } from "@/components/ui/fields";
import { FilterBar } from "@/components/ui/filter-bar";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { Section } from "@/components/ui/section";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { COMPANIES_PATH, MAX_QUERY_LENGTH, parseCompaniesList } from "@/lib/companies-view";

export const metadata = { title: "Companies" };

const ERROR_ID = "companies-filter-errors";

export default async function CompaniesPage({ searchParams }: PageProps<"/companies">) {
  const state = parseCompaniesList(await searchParams);
  if (state.redirect && state.canonical) redirect(state.canonical);

  return (
    <PageContainer width="table">
      <PageHeader
        eyebrow="Evidence"
        title="Companies"
        description="Search the companies stored in this workspace and open one for its revenue, net income and diluted EPS, each traced to the SEC filing it came from. Search never contacts a provider."
      />
      <FilterBar action={COMPANIES_PATH} label="Search companies" clearHref={state.filtered ? COMPANIES_PATH : undefined}>
        <TextField
          id="q"
          label="Name, ticker or CIK"
          defaultValue={state.shown}
          maxLength={MAX_QUERY_LENGTH}
          width="w-64"
          invalid={state.errors.some((e) => e.param === "q")}
          describedBy={ERROR_ID}
        />
      </FilterBar>
      {!state.filtered && state.errors.length === 0 && state.page === 1 ? (
        <Section
          title="Popular companies"
          note="A curated list in alphabetical order by ticker, not a ranking."
        >
          <p className="type-meta max-w-3xl">
            This is a fixed list, not live trending data: QuantForge has no market-popularity, price, volume or sentiment source, and being listed is
            not a recommendation. Figures are descriptive and come from stored SEC filings. Only companies that file 10-K and 10-Q reports with the SEC
            can appear; private companies have no such filings.
          </p>
          <Suspense fallback={<PopularSkeleton />}>
            <PopularCompanies />
          </Suspense>
        </Section>
      ) : null}
      {state.errors.length > 0 ? (
        <FilterErrors id={ERROR_ID} errors={state.errors} />
      ) : (
        <Section title={state.filtered ? "Search results" : "All stored companies"}>
          <Suspense key={state.canonical} fallback={<TableSkeleton label="Loading companies" />}>
            <CompaniesResults state={state} />
          </Suspense>
        </Section>
      )}
    </PageContainer>
  );
}
