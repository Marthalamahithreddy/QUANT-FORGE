import { AppLink } from "@/components/ui/app-link";

import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";

export const metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <PageContainer>
      <EmptyState
        page
        title="This page does not exist"
        action={
          <AppLink href="/" className="text-accent hover:underline">
            Go to the overview
          </AppLink>
        }
      >
        The address may be mistyped, or the page is not part of the interface.
      </EmptyState>
    </PageContainer>
  );
}
