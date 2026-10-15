import { redirect } from "next/navigation";
import { Suspense } from "react";

import { CompaniesResults } from "@/components/companies/companies-results";
import { FilterErrors } from "@/components/ui/filter-errors";
import { TextField } from "@/components/ui/fields";
import { FilterBar } from "@/components/ui/filter-bar";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
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
      {state.errors.length > 0 ? (
        <FilterErrors id={ERROR_ID} errors={state.errors} />
      ) : (
        <Suspense key={state.canonical} fallback={<TableSkeleton label="Loading companies" />}>
          <CompaniesResults state={state} />
        </Suspense>
      )}
    </PageContainer>
  );
}
