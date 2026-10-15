import { CompaniesTable } from "@/components/companies/companies-table";
import { ApiFailureState } from "@/components/ui/api-failure";
import { ListEmpty } from "@/components/ui/list-empty";
import { Pagination } from "@/components/ui/pagination";
import { getCompanies } from "@/lib/api/companies";
import { COMPANIES_PATH, companiesPageHref, type CompaniesListState } from "@/lib/companies-view";
import { MAX_PAGE } from "@/lib/list-query";

/** The data region of the company directory: one API request, then one of the distinct states. */
export async function CompaniesResults({ state }: { readonly state: CompaniesListState }) {
  const result = await getCompanies(state.query);
  const retry = state.canonical ?? COMPANIES_PATH;
  if (result.kind !== "ok") {
    return <ApiFailureState failure={result} subject="the company directory" retryHref={retry} />;
  }
  const { items, total, limit, offset } = result.data;
  if (items.length === 0) {
    return (
      <ListEmpty
        total={total}
        rows={items.length}
        filtered={state.filtered}
        clearHref={COMPANIES_PATH}
        firstPageHref={companiesPageHref(state.query, 1)}
        noun="stored companies"
        noData={{
          title: "No companies are stored",
          body: "Companies appear here once reference data has been ingested. Nothing is fetched when you search.",
        }}
        noMatch={{
          title: "No stored company matches this search",
          body: "Search matches a name, an exact current ticker or a CIK among stored companies only. It never asks a provider.",
        }}
      />
    );
  }
  return (
    <>
      <CompaniesTable companies={items} offset={offset} />
      <Pagination
        total={total}
        limit={limit}
        offset={offset}
        maxPage={MAX_PAGE}
        hrefFor={(page) => companiesPageHref(state.query, page)}
        noun="stored companies"
      />
    </>
  );
}
