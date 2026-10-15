import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/** The loading shape of the Popular companies grid: one decorative card per featured company. */
export function PopularSkeleton({ cards = 10 }: { readonly cards?: number }) {
  return (
    <SkeletonRegion label="Loading popular companies">
      <div className="grid gap-3 md:grid-cols-2">
        {Array.from({ length: cards }, (_, index) => (
          <div key={index} className="flex flex-col gap-3 rounded border border-border bg-surface p-4">
            <Skeleton width="12rem" height="1rem" />
            <Skeleton width="16rem" height="0.75rem" />
            <Skeleton width="100%" height="0.75rem" />
            <Skeleton width="100%" height="0.75rem" />
            <Skeleton width="100%" height="0.75rem" />
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}
