import { AppShell } from "@/components/layout/app-shell";
import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { Section } from "@/components/ui/section";
import { getBackendStatus } from "@/lib/api/health";

export const metadata = { title: "Research" };

const PRINCIPLES = [
  {
    title: "Point-in-time discipline",
    body: "An observation is treated as known at a time only when the source states when it was published. A timestamp with no stated basis, or with an unapproved basis, does not qualify.",
  },
  {
    title: "Descriptive versus point-in-time",
    body: "Retrospective questions may use observations with no availability time. Questions about what could have been known and acted on at a given moment may not.",
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

export default async function ResearchPage() {
  const status = await getBackendStatus();

  return (
    <AppShell status={status}>
      <PageContainer>
        <PageHeader
          eyebrow="Research"
          title="Research"
          description="How the research engine behaves today. This page reports principles, not results."
        />

        <EmptyState title="The event-study workspace is not part of the interface yet">
          The API serves research observations, but this interface does not display them yet, so
          no returns are shown here. No sample or illustrative returns are displayed.
        </EmptyState>

        <Section title="Engine principles">
          <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
            {PRINCIPLES.map((item) => (
              <div key={item.title}>
                <dt className="text-sm font-medium text-text">{item.title}</dt>
                <dd className="mt-1 text-sm leading-relaxed text-text-2">{item.body}</dd>
              </div>
            ))}
          </dl>
        </Section>
      </PageContainer>
    </AppShell>
  );
}
