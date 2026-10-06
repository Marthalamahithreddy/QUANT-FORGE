import { notFound } from "next/navigation";
import Link from "next/link";

import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { decode, uuid } from "@/lib/api/decode";

export const metadata = { title: "Research workspace" };

/**
 * The address the earnings "Open research workspace" action targets. The workspace itself is a
 * later slice (11.1D); until then this route says so plainly and shows nothing computed. It makes
 * no API request and chooses no benchmark or filing: the event identity in the address is all
 * there is.
 */
export default async function ResearchEventPage({ params }: PageProps<"/research/events/[eventId]">) {
  const { eventId } = await params;
  if (!decode(uuid, eventId).ok) notFound();

  return (
    <PageContainer>
      <Breadcrumbs items={[{ label: "Research", href: "/research" }, { label: "Event study" }]} />
      <PageHeader eyebrow="Research" title="Event study" description="The research workspace for one stored earnings event." />
      <EmptyState
        title="The research workspace is not part of the interface yet"
        action={
          <Link href={`/earnings/${eventId}`} className="text-accent hover:underline">
            Back to the earnings event
          </Link>
        }
      >
        The API serves research observations for this event, but this interface does not display them yet, so no
        returns are shown. No benchmark or filing has been chosen, and none is assumed.
      </EmptyState>
    </PageContainer>
  );
}
