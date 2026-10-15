import { PageContainer } from "@/components/ui/page-container";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/** The company view's shape: header, three metric cards, the comparison table. */
export default function Loading() {
  return (
    <PageContainer width="workspace">
      <SkeletonRegion label="Loading company">
        <Skeleton width="12rem" height="0.75rem" />
        <div className="flex flex-col gap-2 border-b border-border-subtle pb-4">
          <Skeleton width="8rem" height="0.75rem" />
          <Skeleton width="14rem" height="1.5rem" />
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          <Skeleton height="11rem" />
          <Skeleton height="11rem" />
          <Skeleton height="11rem" />
        </div>
        <Skeleton height="8rem" />
      </SkeletonRegion>
    </PageContainer>
  );
}
