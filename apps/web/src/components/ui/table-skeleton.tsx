import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

export interface TableSkeletonProps {
  /** What is loading, announced politely ("Loading earnings events"). */
  readonly label: string;
  readonly rows?: number;
}

/**
 * The loading shape of a `DataTable`: a header row and body rows at the table's real row height,
 * so the table does not jump when it arrives. Decorative blocks; the region announces itself.
 */
export function TableSkeleton({ label, rows = 10 }: TableSkeletonProps) {
  return (
    <SkeletonRegion label={label}>
      <div className="rounded border border-border">
        <div className="border-b border-border px-3 py-2">
          <Skeleton width="14rem" height="0.75rem" />
        </div>
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="flex gap-6 border-b border-border-subtle px-3 py-2 last:border-b-0">
            <Skeleton width="6rem" height="1rem" />
            <Skeleton width="10rem" height="1rem" />
            <Skeleton width="5rem" height="1rem" className="hidden sm:block" />
            <Skeleton width="4rem" height="1rem" className="hidden md:block" />
            <Skeleton width="4rem" height="1rem" className="hidden md:block" />
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}
