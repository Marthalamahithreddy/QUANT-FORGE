import Link from "next/link";

import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";
import { SAVED_PATH } from "@/lib/record-view";

export default function SavedRecordNotFound() {
  return (
    <PageContainer>
      <EmptyState
        title="No saved research record has this identifier"
        action={
          <Link href={SAVED_PATH} className="text-accent hover:underline">
            Back to saved research
          </Link>
        }
      >
        The record may not exist, or the address was mistyped. Records are listed on the saved research page.
      </EmptyState>
    </PageContainer>
  );
}
