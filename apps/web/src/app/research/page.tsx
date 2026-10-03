import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { StatePanel } from "@/components/ui/state-panel";
import { getBackendStatus } from "@/lib/api/health";

export const metadata = { title: "Research · QuantForge" };

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
      <div className="mx-auto flex max-w-4xl flex-col gap-6">
        <PageHeader
          eyebrow="Research"
          title="Research"
          description="How the research engine behaves today. This page reports principles, not results."
        />

        <StatePanel kind="empty" title="No research results are available here yet">
          Event-study and signal outputs exist in the backend but are not exposed through the
          API, so nothing is shown. No sample or illustrative returns are displayed.
        </StatePanel>

        <section aria-label="Engine principles" className="grid gap-4 sm:grid-cols-2">
          {PRINCIPLES.map((item) => (
            <div key={item.title} className="rounded-md border border-border-subtle bg-surface-1 p-4">
              <h2 className="text-sm font-medium text-text-primary">{item.title}</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-text-secondary">{item.body}</p>
            </div>
          ))}
        </section>
      </div>
    </AppShell>
  );
}
