import { AppLink } from "@/components/ui/app-link";

import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";

export default function CompanyNotFound() {
  return (
    <PageContainer>
      <EmptyState
        page
        title="No stored company has this CIK"
        action={
          <AppLink href="/companies" className="text-accent hover:underline">
            Back to companies
          </AppLink>
        }
      >
        The company may never have been stored, or the address was mistyped. Nothing was looked up elsewhere.
      </EmptyState>
    </PageContainer>
  );
}
