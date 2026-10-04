import { AppShell } from "@/components/layout/app-shell";
import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { getBackendStatus } from "@/lib/api/health";

export const metadata = { title: "Saved research" };

/**
 * A placeholder until the saved-research list exists (11.1E). It states what is true and shows no
 * rows: nothing is listed, nothing is invented, and no save action exists.
 */
export default async function SavedResearchPage() {
  const status = await getBackendStatus();

  return (
    <AppShell status={status}>
      <PageContainer>
        <PageHeader
          eyebrow="Research"
          title="Saved research"
          description="Stored baselines of completed research computations, with their reproducibility level."
        />
        <EmptyState title="The saved-research list is not part of the interface yet">
          Baselines are created by an internal service; the interface has no save action, and none
          are displayed here yet.
        </EmptyState>
      </PageContainer>
    </AppShell>
  );
}
