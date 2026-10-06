import { redirect } from "next/navigation";
import { Suspense } from "react";

import { FilingsResults } from "@/components/filings/filings-results";
import { FilterErrors, hasError } from "@/components/ui/filter-errors";
import { SelectField, TextField } from "@/components/ui/fields";
import { FilterBar } from "@/components/ui/filter-bar";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { FILING_FORMS } from "@/lib/api/schema";
import { FILINGS_PATH, parseFilingsList } from "@/lib/filings-view";

export const metadata = { title: "Filings" };

const ERROR_ID = "filings-filter-errors";

export default async function FilingsPage({ searchParams }: PageProps<"/filings">) {
  const state = parseFilingsList(await searchParams);
  // One address per view: empty parameters, `page=1` and unknown parameters are dropped.
  if (state.redirect && state.canonical) redirect(state.canonical);
  const invalid = (param: string) => hasError(state.errors, param);

  return (
    <PageContainer width="table">
      <PageHeader
        eyebrow="Evidence"
        title="Filings"
        description="Stored filings that have SEC FSDS financial-fact data, newest filing date first. This is not every SEC filing, and it says nothing about when a filing was available or tradable. Filing and report dates are source metadata."
      />

      <FilterBar action={FILINGS_PATH} label="Filter filings" clearHref={state.filtered ? FILINGS_PATH : undefined}>
        <SelectField
          id="form"
          label="Form (exact match)"
          defaultValue={state.shown.form}
          invalid={invalid("form")}
          describedBy={ERROR_ID}
        >
          <option value="">Any form</option>
          {FILING_FORMS.map((form) => (
            <option key={form} value={form}>
              {form}
            </option>
          ))}
        </SelectField>
        <TextField
          id="cik"
          label="CIK (ten digits)"
          defaultValue={state.shown.cik}
          maxLength={10}
          inputMode="numeric"
          pattern="[0-9]{10}"
          title="Exactly ten digits, including leading zeros"
          mono
          width="w-32"
          invalid={invalid("cik")}
          describedBy={ERROR_ID}
        />
      </FilterBar>
      <p className="type-meta -mt-3">Filters match exactly: 10-Q does not include 10-Q/A.</p>

      {state.errors.length > 0 ? (
        <FilterErrors id={ERROR_ID} errors={state.errors} />
      ) : (
        <Suspense key={state.canonical} fallback={<TableSkeleton label="Loading filings" />}>
          <FilingsResults state={state} />
        </Suspense>
      )}
    </PageContainer>
  );
}
