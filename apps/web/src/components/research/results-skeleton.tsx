import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/** The result region's shape while the observation is read: status strip, then the two mode frames. */
export function ResearchResultsSkeleton() {
  return (
    <SkeletonRegion label="Loading the research observation">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <Skeleton key={index} height="3.5rem" />
        ))}
      </div>
      <Skeleton height="11rem" />
      <Skeleton height="26rem" />
      <Skeleton height="12rem" />
    </SkeletonRegion>
  );
}
