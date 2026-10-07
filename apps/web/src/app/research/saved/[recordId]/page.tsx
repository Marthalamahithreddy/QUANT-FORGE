import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { RecordDetailView } from "@/components/records/record-detail";
import { RecordFailure } from "@/components/records/record-failure";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { decode, uuid } from "@/lib/api/decode";
import { getRecord } from "@/lib/api/records";
import { SAVED_PATH, parseRecordView, recordHref, recordPath } from "@/lib/record-view";
import { shortId } from "@/lib/record-wording";

export async function generateMetadata({ params }: PageProps<"/research/saved/[recordId]">): Promise<Metadata> {
  const { recordId } = await params;
  return { title: decode(uuid, recordId).ok ? `Record ${shortId(recordId)} · Saved research` : "Saved research record" };
}

export default async function SavedRecordPage({ params, searchParams }: PageProps<"/research/saved/[recordId]">) {
  const { recordId } = await params;
  // A path that is not a UUID names no record: not-found, and no request is made.
  if (!decode(uuid, recordId).ok) notFound();
  const raw = await searchParams;
  // One address per record: a UUID is lower-cased, keeping an explicit verify request.
  if (recordId !== recordId.toLowerCase()) redirect(recordHref(recordId.toLowerCase(), parseRecordView(recordId, raw).verify));

  const state = parseRecordView(recordId, raw);
  // One address per view: `verify=` and unknown parameters are dropped.
  if (state.redirect && state.canonical) redirect(state.canonical);

  const result = await getRecord(recordId);
  if (result.kind === "not_found") notFound();

  const crumbs = [{ label: "Research", href: "/research" }, { label: "Saved research", href: SAVED_PATH }, { label: `Record ${shortId(recordId)}` }];

  if (result.kind !== "ok") {
    return (
      <PageContainer>
        <Breadcrumbs items={crumbs} />
        <PageHeader eyebrow="Saved research" title="Saved research record" />
        <RecordFailure failure={result} subject="this saved record" retryHref={state.canonical ?? recordPath(recordId)} />
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      <Breadcrumbs items={crumbs} />
      <PageHeader
        eyebrow="Saved research"
        title="Saved research record"
        description="A stored historical baseline: what was asked, the reproducibility of its evidence and the result that was persisted. Nothing on this page recomputes it."
      />
      <RecordDetailView record={result.data} state={state} />
    </PageContainer>
  );
}
