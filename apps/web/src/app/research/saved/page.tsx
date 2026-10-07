import { redirect } from "next/navigation";
import { Suspense } from "react";

import { SavedRecordsResults } from "@/components/records/records-results";
import { FilterErrors } from "@/components/ui/filter-errors";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { parseSavedList } from "@/lib/record-view";

export const metadata = { title: "Saved research" };

const ERROR_ID = "saved-list-errors";

export default async function SavedResearchPage({ searchParams }: PageProps<"/research/saved">) {
  const state = parseSavedList(await searchParams);
  // One address per view: `page=1`, an empty `page=` and unknown parameters are dropped.
  if (state.redirect && state.canonical) redirect(state.canonical);

  return (
    <PageContainer width="table">
      <PageHeader
        eyebrow="Research"
        title="Saved research"
        description="Immutable baselines of completed research computations, newest saved first. Each is a stored historical record with its reproducibility level; opening one shows what was stored and can, on request, compare it with a re-run against today's stored evidence. This page lists records that already exist: it cannot save one."
      />
      {state.errors.length > 0 ? (
        <FilterErrors id={ERROR_ID} errors={state.errors} title="This page address is not valid" />
      ) : (
        // Keyed by the canonical address, so a page change shows the skeleton instead of old rows.
        <Suspense key={state.canonical} fallback={<TableSkeleton label="Loading saved research baselines" rows={8} />}>
          <SavedRecordsResults state={state} />
        </Suspense>
      )}
    </PageContainer>
  );
}
