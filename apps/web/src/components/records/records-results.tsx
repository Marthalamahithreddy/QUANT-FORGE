import Link from "next/link";

import { RecordFailure } from "@/components/records/record-failure";
import { RecordsTable } from "@/components/records/records-table";
import { EmptyState } from "@/components/ui/empty-state";
import { Pagination } from "@/components/ui/pagination";
import { getRecordPage } from "@/lib/api/records";
import { RECORD_PAGE_SIZE } from "@/lib/api/schema";
import { MAX_PAGE } from "@/lib/list-query";
import { SAVED_PATH, offsetOfRecordPage, savedPageHref, type SavedListState } from "@/lib/record-view";

/**
 * The data region of the saved-research list: ONE list request, then one of the distinct states
 * (transport failure, contract failure, integrity failure, nothing saved, past the last page, or
 * rows). It requests no record detail and no verification; the only other reads are the bounded
 * current-event lookups inside the table, one per distinct event on the page.
 */
export async function SavedRecordsResults({ state }: { readonly state: SavedListState }) {
  const result = await getRecordPage({ limit: RECORD_PAGE_SIZE, offset: offsetOfRecordPage(state.page) });
  const retry = state.canonical ?? SAVED_PATH;

  if (result.kind !== "ok") {
    return <RecordFailure failure={result} subject="the saved research baselines" retryHref={retry} />;
  }
  const { items, total, limit, offset } = result.data;

  if (total === 0) {
    return (
      <EmptyState
        title="No saved research baselines are stored"
        action={
          <Link href="/research" className="text-accent hover:underline">
            Open Research
          </Link>
        }
      >
        This page lists baselines that have already been persisted; it does not create them, and this interface cannot save one. Nothing is shown in the
        meantime.
      </EmptyState>
    );
  }
  if (items.length === 0) {
    return (
      <EmptyState
        title="This page is past the last result"
        action={
          <Link href={SAVED_PATH} className="text-accent hover:underline">
            Go to the first page
          </Link>
        }
      >
        There are {total} saved baselines, and none at this position.
      </EmptyState>
    );
  }

  return (
    <>
      <RecordsTable records={items} />
      <Pagination total={total} limit={limit} offset={offset} maxPage={MAX_PAGE} hrefFor={savedPageHref} noun="saved baselines" />
    </>
  );
}
