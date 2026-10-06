import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EventDetail } from "@/components/earnings/event-detail";
import { ApiFailureState } from "@/components/ui/api-failure";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { getEarningsEventOnce } from "@/lib/api/earnings";

export async function generateMetadata({ params }: PageProps<"/earnings/[id]">): Promise<Metadata> {
  const result = await getEarningsEventOnce((await params).id);
  return {
    title:
      result.kind === "ok"
        ? `${result.data.company.display_name} · ${result.data.event_date}`
        : "Earnings event",
  };
}

export default async function EarningsDetailPage({ params }: PageProps<"/earnings/[id]">) {
  const { id } = await params;
  const result = await getEarningsEventOnce(id);

  if (result.kind === "not_found") notFound();

  if (result.kind !== "ok") {
    return (
      <PageContainer>
        <Breadcrumbs items={[{ label: "Earnings", href: "/earnings" }, { label: "Event" }]} />
        <PageHeader eyebrow="Earnings observation" title="Earnings event" />
        <ApiFailureState failure={result} subject="this earnings event" retryHref={`/earnings/${encodeURIComponent(id)}`} />
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      <Breadcrumbs
        items={[
          { label: "Earnings", href: "/earnings" },
          { label: `${result.data.company.display_name} · ${result.data.event_date}` },
        ]}
      />
      <EventDetail event={result.data} />
    </PageContainer>
  );
}
