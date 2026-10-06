import Link from "next/link";

import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";

export default function ResearchEventNotFound() {
  return (
    <PageContainer>
      <EmptyState
        title="No stored event has this identifier"
        action={
          <Link href="/earnings" className="text-accent hover:underline">
            Find an event in the earnings list
          </Link>
        }
      >
        The event may never have been stored, or the address was mistyped. A study needs one stored earnings event.
      </EmptyState>
    </PageContainer>
  );
}
