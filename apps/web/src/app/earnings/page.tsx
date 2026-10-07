import { redirect } from "next/navigation";
import { Suspense } from "react";

import { EarningsResults } from "@/components/earnings/earnings-results";
import { FilterErrors, hasError } from "@/components/ui/filter-errors";
import { TextField } from "@/components/ui/fields";
import { FilterBar } from "@/components/ui/filter-bar";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { EARNINGS_PATH, parseEarningsList } from "@/lib/earnings-view";

export const metadata = { title: "Earnings" };

const ERROR_ID = "earnings-filter-errors";

export default async function EarningsPage({ searchParams }: PageProps<"/earnings">) {
  const state = parseEarningsList(await searchParams);
  // One address per view: empty parameters, `page=1` and unknown parameters are dropped.
  if (state.redirect && state.canonical) redirect(state.canonical);
  const invalid = (param: string) => hasError(state.errors, param);

  return (
    <PageContainer width="table">
      <PageHeader
        eyebrow="Evidence"
        title="Earnings"
        description="Stored earnings events with their availability evidence, newest event date first. Each row is one stored event: a revision or a second source for the same fiscal period appears as its own row. Point-in-time status is the backend's classification; this page does not recompute it."
      />

      <FilterBar action={EARNINGS_PATH} label="Filter earnings" clearHref={state.filtered ? EARNINGS_PATH : undefined}>
        <TextField
          id="ticker"
          label="Ticker (proven on the event date)"
          defaultValue={state.shown.ticker}
          maxLength={32}
          mono
          width="w-32"
          invalid={invalid("ticker")}
          describedBy={ERROR_ID}
        />
        <TextField
          id="date_from"
          type="date"
          label="From"
          defaultValue={state.shown.date_from}
          invalid={invalid("date_from")}
          describedBy={ERROR_ID}
        />
        <TextField
          id="date_to"
          type="date"
          label="To"
          defaultValue={state.shown.date_to}
          invalid={invalid("date_to")}
          describedBy={ERROR_ID}
        />
      </FilterBar>

      {state.errors.length > 0 ? (
        <FilterErrors id={ERROR_ID} errors={state.errors} />
      ) : (
        // Keyed by the canonical address: a filter or page change is a new boundary, so the
        // skeleton shows again instead of the old rows lingering under new filters.
        <Suspense key={state.canonical} fallback={<TableSkeleton label="Loading earnings events" />}>
          <EarningsResults state={state} />
        </Suspense>
      )}
    </PageContainer>
  );
}
