import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import { TableSkeleton } from "@/components/ui/table-skeleton";

/** Direct load of the saved list: the header's shape, then the table skeleton. */
export default function Loading() {
  return (
    <PageContainer width="table">
      <div className="flex flex-col gap-2 border-b border-border-subtle pb-4">
        <Skeleton width="5rem" height="0.75rem" />
        <Skeleton width="12rem" height="1.5rem" />
      </div>
      <TableSkeleton label="Loading saved research baselines" rows={8} />
    </PageContainer>
  );
}
