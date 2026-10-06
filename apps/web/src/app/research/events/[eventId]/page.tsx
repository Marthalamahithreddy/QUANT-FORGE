import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";

import { ContextBar } from "@/components/research/context-bar";
import { ResearchConfigForm } from "@/components/research/research-config-form";
import { ResearchResults } from "@/components/research/research-results";
import { ResearchResultsSkeleton } from "@/components/research/results-skeleton";
import { ApiFailureState } from "@/components/ui/api-failure";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { FilterErrors } from "@/components/ui/filter-errors";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { decode, uuid } from "@/lib/api/decode";
import { getEarningsEventOnce } from "@/lib/api/earnings";
import { parseResearchConfig } from "@/lib/research-view";

export async function generateMetadata({ params }: PageProps<"/research/events/[eventId]">): Promise<Metadata> {
  const { eventId } = await params;
  const result = decode(uuid, eventId).ok ? await getEarningsEventOnce(eventId) : null;
  return { title: result?.kind === "ok" ? `Research · ${result.data.company.display_name} ${result.data.event_date}` : "Research" };
}

export default async function ResearchEventPage({ params, searchParams }: PageProps<"/research/events/[eventId]">) {
  const { eventId } = await params;
  // A malformed identifier names nothing: not found, and no request is made.
  if (!decode(uuid, eventId).ok) notFound();

  const config = parseResearchConfig(eventId, await searchParams);
  // One address per study: defaults, empty values, unsorted horizons and unknown parameters are
  // normalised away. Done before any API read.
  if (config.redirect && config.canonical) redirect(config.canonical);

  const record = await getEarningsEventOnce(eventId);
  if (record.kind === "not_found") notFound();
  if (record.kind !== "ok") {
    return (
      <PageContainer width="workspace">
        <Breadcrumbs items={[{ label: "Research", href: "/research" }, { label: "Event study" }]} />
        <PageHeader eyebrow="Research" title="Event study" />
        <ApiFailureState failure={record} subject="this earnings event" retryHref={config.canonical ?? undefined} />
      </PageContainer>
    );
  }

  const event = record.data;
  return (
    <PageContainer width="workspace">
      <Breadcrumbs items={[{ label: "Research", href: "/research" }, { label: `${event.company.display_name} · ${event.event_date}` }]} />
      <PageHeader
        eyebrow="Research · event study"
        title={event.company.display_name}
        description="Descriptive returns around this stored event, the separate point-in-time verdict, optional accounting from a filing you supply, and every refusal. Not a signal, forecast or recommendation."
      />
      <ContextBar eventId={eventId} record={event} config={config} />
      <ResearchConfigForm eventId={eventId} config={config} companyCik={event.company.cik} />

      {config.errors.length > 0 ? (
        <FilterErrors id="research-config-errors" errors={config.errors} title="This configuration cannot be used. No request was made." />
      ) : (
        // Keyed by the study's identity: a changed configuration is a new boundary, so the skeleton
        // shows again instead of the old study lingering under new settings.
        <Suspense key={config.studyKey} fallback={<ResearchResultsSkeleton />}>
          <ResearchResults eventId={eventId} record={event} config={config} />
        </Suspense>
      )}
    </PageContainer>
  );
}
