import { FilingsTable } from "@/components/filings/filings-table";
import { ApiFailureState } from "@/components/ui/api-failure";
import { ListEmpty } from "@/components/ui/list-empty";
import { Pagination } from "@/components/ui/pagination";
import { getFilings } from "@/lib/api/filings";
import { FILINGS_PATH, filingsPageHref, type FilingsListState } from "@/lib/filings-view";
import { MAX_PAGE } from "@/lib/list-query";

/** The data region of the filings list: one API request, then one of the distinct states. */
export async function FilingsResults({ state }: { readonly state: FilingsListState }) {
  const result = await getFilings(state.query);
  const retry = state.canonical ?? FILINGS_PATH;

  if (result.kind !== "ok") {
    return <ApiFailureState failure={result} subject="the filings index" retryHref={retry} />;
  }
  const { items, total, limit, offset } = result.data;

  if (items.length === 0) {
    return (
      <ListEmpty
        total={total}
        rows={items.length}
        filtered={state.filtered}
        clearHref={FILINGS_PATH}
        firstPageHref={filingsPageHref(state.query, 1)}
        noun="stored filings"
        noData={{
          title: "No FSDS-backed filings are stored",
          body: "Filings appear here once SEC FSDS financial facts have been ingested for them. This does not mean no filings exist.",
        }}
        noMatch={{
          title: "No stored filings match these filters",
          body: "No FSDS-backed filing matches. Clear the filters to see every stored filing.",
        }}
      />
    );
  }

  return (
    <>
      <FilingsTable filings={items} offset={offset} />
      <Pagination
        total={total}
        limit={limit}
        offset={offset}
        maxPage={MAX_PAGE}
        hrefFor={(page) => filingsPageHref(state.query, page)}
        noun="stored filings"
      />
      <p className="type-meta max-w-3xl">
        A joint accession stored under two registrants appears as two rows, told apart by CIK. A dash
        means &ldquo;Not stated&rdquo;: the API gave no value. The FSDS period end is given only when all of a
        filing&rsquo;s FSDS submissions state the same period. Paging is by offset: filings ingested while you
        browse can shift later pages.
      </p>
    </>
  );
}
