import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import { TableSkeleton } from "@/components/ui/table-skeleton";

/** Direct load of the list: the header and filter shapes, then the table skeleton. */
export default function Loading() {
  return (
    <PageContainer width="table">
      <div className="flex flex-col gap-2 border-b border-border-subtle pb-4">
        <Skeleton width="5rem" height="0.75rem" />
        <Skeleton width="8rem" height="1.5rem" />
      </div>
      <Skeleton height="3.5rem" width="min(30rem, 100%)" />
      <TableSkeleton label="Loading filings" />
    </PageContainer>
  );
}
