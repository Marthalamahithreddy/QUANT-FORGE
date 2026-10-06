import { PageContainer } from "@/components/ui/page-container";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/** The filing view's shape: header, selectors, then the three EPS frames. */
export default function Loading() {
  return (
    <PageContainer>
      <SkeletonRegion label="Loading filing">
        <Skeleton width="14rem" height="0.75rem" />
        <div className="flex flex-col gap-2 border-b border-border-subtle pb-4">
          <Skeleton width="6rem" height="0.75rem" />
          <Skeleton width="15rem" height="1.5rem" />
        </div>
        <Skeleton height="2.25rem" width="min(24rem, 100%)" />
        <Skeleton height="9rem" />
        <Skeleton height="9rem" />
        <Skeleton height="7rem" />
      </SkeletonRegion>
    </PageContainer>
  );
}
