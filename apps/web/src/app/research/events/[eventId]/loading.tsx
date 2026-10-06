import { ResearchResultsSkeleton } from "@/components/research/results-skeleton";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";

/** Direct load: the page identity and the configuration's shape first, then the result region. */
export default function Loading() {
  return (
    <PageContainer width="workspace">
      <Skeleton width="16rem" height="0.75rem" />
      <div className="flex flex-col gap-2 border-b border-border-subtle pb-4">
        <Skeleton width="8rem" height="0.75rem" />
        <Skeleton width="16rem" height="1.5rem" />
      </div>
      <Skeleton height="4.5rem" />
      <Skeleton height="9rem" />
      <ResearchResultsSkeleton />
    </PageContainer>
  );
}
