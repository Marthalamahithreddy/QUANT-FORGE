import { AppLink } from "@/components/ui/app-link";

import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";

export default function EarningsEventNotFound() {
  return (
    <PageContainer>
      <EmptyState
        page
        title="No stored event has this identifier"
        action={
          <AppLink href="/earnings" className="text-accent hover:underline">
            Back to earnings
          </AppLink>
        }
      >
        The event may never have been stored, or the address was mistyped.
      </EmptyState>
    </PageContainer>
  );
}
