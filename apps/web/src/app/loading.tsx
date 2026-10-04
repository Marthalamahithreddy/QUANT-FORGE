import { AppShell } from "@/components/layout/app-shell";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/**
 * The route fallback: the shell paints at once (status "Checking API") while the page's data is
 * read. Structure stays visible; there is no full-page spinner. Route slices replace the body
 * with a skeleton that matches their final layout.
 */
export default function Loading() {
  return (
    <AppShell>
      <PageContainer>
        <SkeletonRegion label="Loading page">
          <Skeleton width="12rem" height="1.5rem" />
          <Skeleton width="min(36rem, 100%)" height="0.875rem" />
          <Skeleton height="14rem" className="mt-2" />
        </SkeletonRegion>
      </PageContainer>
    </AppShell>
  );
}
