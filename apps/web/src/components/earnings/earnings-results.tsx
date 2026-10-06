import { EarningsTable } from "@/components/earnings/earnings-table";
import { ApiFailureState } from "@/components/ui/api-failure";
import { ListEmpty } from "@/components/ui/list-empty";
import { Pagination } from "@/components/ui/pagination";
import { getEarningsPage } from "@/lib/api/earnings";
import { earningsPageHref, type EarningsListState, EARNINGS_PATH } from "@/lib/earnings-view";
import { MAX_PAGE } from "@/lib/list-query";

/**
 * The data region of the earnings list: ONE API request, then one of the distinct states
 * (transport failure, contract failure, nothing stored, filters matched nothing, past the last
 * page, or rows). The page paints its header and filters first; this streams in behind them.
 */
export async function EarningsResults({ state }: { readonly state: EarningsListState }) {
  const result = await getEarningsPage(state.query);
  const retry = state.canonical ?? EARNINGS_PATH;

  if (result.kind !== "ok") {
    return <ApiFailureState failure={result} subject="the earnings events" retryHref={retry} />;
  }
  const { items, total, limit, offset } = result.data;

  if (items.length === 0) {
    return (
      <ListEmpty
      total={total}
      rows={items.length}
      filtered={state.filtered}
      clearHref={EARNINGS_PATH}
      firstPageHref={earningsPageHref(state.query, 1)}
      noun="stored observations"
      noData={{ title: "No earnings events are stored", body: "Events appear here after ingestion has loaded them. Nothing is shown in the meantime." }}
      noMatch={{ title: "Nothing matches these filters", body: "No stored earnings observation matches. Clear the filters to see every stored one." }}
      />
    );
  }

  return (
    <>
      <EarningsTable events={items} />
      <Pagination
        total={total}
        limit={limit}
        offset={offset}
        maxPage={MAX_PAGE}
        hrefFor={(page) => earningsPageHref(state.query, page)}
        noun="stored observations"
      />
    </>
  );
}
