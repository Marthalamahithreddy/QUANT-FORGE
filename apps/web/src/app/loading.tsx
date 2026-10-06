import { PageContainer } from "@/components/ui/page-container";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/**
 * The generic route fallback: only the PAGE region. The shell is rendered by the layout and is
 * already on screen, so nothing here repeats it. Routes with a recognisable layout provide their
 * own `loading.tsx` with a skeleton of that layout.
 */
export default function Loading() {
  return (
    <PageContainer>
      <SkeletonRegion label="Loading page">
        <Skeleton width="12rem" height="1.5rem" />
        <Skeleton width="min(36rem, 100%)" height="0.875rem" />
        <Skeleton height="14rem" className="mt-2" />
      </SkeletonRegion>
    </PageContainer>
  );
}
