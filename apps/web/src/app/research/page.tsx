import { AppLink } from "@/components/ui/app-link";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { EventFinder, RESEARCH_PATH } from "@/components/research/event-finder";
import { TextField } from "@/components/ui/fields";
import { FilterBar } from "@/components/ui/filter-bar";
import { FilterErrors, hasError } from "@/components/ui/filter-errors";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { Section } from "@/components/ui/section";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import { parseEarningsList } from "@/lib/earnings-view";

export const metadata = { title: "Research" };

const PRINCIPLES = [
  {
    title: "Point-in-time discipline",
    body: "A stored event is treated as known at a time only when the source states when it was published. A timestamp with no stated basis, or with an unapproved basis, does not qualify.",
  },
  {
    title: "Descriptive versus point-in-time",
    body: "Retrospective questions may use events with no availability time. Questions about what could have been known and acted on at a given moment may not. A study shows both, separately.",
  },
  {
    title: "Split-adjusted research prices",
    body: "Research reads split-adjusted prices only. As-traded prices are reserved for future execution and accounting.",
  },
  {
    title: "Explicit refusals",
    body: "Where evidence is missing, the engine returns a named status instead of a plausible-looking number.",
  },
] as const;

const ERROR_ID = "research-finder-errors";

export default async function ResearchPage({ searchParams }: PageProps<"/research">) {
  const state = parseEarningsList(await searchParams, RESEARCH_PATH);
  if (state.redirect && state.canonical) redirect(state.canonical);
  const invalid = (param: string) => hasError(state.errors, param);

  return (
    <PageContainer width="table">
      <PageHeader
        eyebrow="Research"
        title="Research"
        description="A study starts from one stored earnings event: the descriptive returns around it, the separate point-in-time verdict, an optional benchmark and an optional filing you supply, with every refusal stated. Find an event below. Nothing here is a signal, forecast or recommendation."
      />

      <FilterBar action={RESEARCH_PATH} label="Find an event" clearHref={state.filtered ? RESEARCH_PATH : undefined}>
        <TextField id="ticker" label="Ticker (proven on the event date)" defaultValue={state.shown.ticker} maxLength={32} mono width="w-32" invalid={invalid("ticker")} describedBy={ERROR_ID} />
        <TextField id="date_from" type="date" label="From" defaultValue={state.shown.date_from} invalid={invalid("date_from")} describedBy={ERROR_ID} />
        <TextField id="date_to" type="date" label="To" defaultValue={state.shown.date_to} invalid={invalid("date_to")} describedBy={ERROR_ID} />
      </FilterBar>

      {state.errors.length > 0 ? (
        <FilterErrors id={ERROR_ID} errors={state.errors} />
      ) : (
        <Suspense key={state.canonical} fallback={<TableSkeleton label="Loading stored events" />}>
          <EventFinder state={state} />
        </Suspense>
      )}

      <p className="type-meta">
        Stored baselines of completed studies are listed under{" "}
        <AppLink href="/research/saved" className="text-accent hover:underline">
          Saved research
        </AppLink>
        .
      </p>

      <details className="rounded border border-border bg-surface">
        <summary className="cursor-pointer rounded px-3 py-2 text-sm font-medium text-text max-lg:min-h-11">How studies work</summary>
        <Section title="Engine principles">
          <dl className="grid gap-x-8 gap-y-4 p-3 sm:grid-cols-2">
            {PRINCIPLES.map((item) => (
              <div key={item.title}>
                <dt className="text-sm font-medium text-text">{item.title}</dt>
                <dd className="mt-1 text-sm leading-relaxed text-text-2">{item.body}</dd>
              </div>
            ))}
          </dl>
        </Section>
      </details>
    </PageContainer>
  );
}
