import { AppLink } from "@/components/ui/app-link";

import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";
import { SAVED_PATH } from "@/lib/record-view";

export default function SavedRecordNotFound() {
  return (
    <PageContainer>
      <EmptyState
        page
        title="No saved research record has this identifier"
        action={
          <AppLink href={SAVED_PATH} className="text-accent hover:underline">
            Back to saved research
          </AppLink>
        }
      >
        The record may not exist, or the address was mistyped. Records are listed on the saved research page.
      </EmptyState>
    </PageContainer>
  );
}
