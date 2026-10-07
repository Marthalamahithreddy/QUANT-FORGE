import { PageContainer } from "@/components/ui/page-container";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/** The stored baseline's shape: breadcrumb, header, the baseline banner, then the identity and request. */
export default function Loading() {
  return (
    <PageContainer>
      <SkeletonRegion label="Loading the stored baseline">
        <Skeleton width="16rem" height="0.75rem" />
        <div className="flex flex-col gap-2 border-b border-border-subtle pb-4">
          <Skeleton width="7rem" height="0.75rem" />
          <Skeleton width="16rem" height="1.5rem" />
        </div>
        <Skeleton height="4rem" />
        <Skeleton height="11rem" />
        <Skeleton height="12rem" />
      </SkeletonRegion>
    </PageContainer>
  );
}
