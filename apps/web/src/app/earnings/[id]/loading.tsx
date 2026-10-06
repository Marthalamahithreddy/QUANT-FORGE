import { PageContainer } from "@/components/ui/page-container";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/** The event record's shape: a header, then the two evidence frames. */
export default function Loading() {
  return (
    <PageContainer>
      <SkeletonRegion label="Loading earnings event">
        <Skeleton width="14rem" height="0.75rem" />
        <div className="flex flex-col gap-2 border-b border-border-subtle pb-4">
          <Skeleton width="6rem" height="0.75rem" />
          <Skeleton width="16rem" height="1.5rem" />
        </div>
        <div className="grid gap-4 xl:grid-cols-2">
          <Skeleton height="22rem" />
          <Skeleton height="16rem" />
        </div>
      </SkeletonRegion>
    </PageContainer>
  );
}
