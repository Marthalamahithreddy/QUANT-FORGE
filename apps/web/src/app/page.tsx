import { Suspense } from "react";

import { LatestEarnings } from "@/components/overview/latest-earnings";
import { LatestFilings } from "@/components/overview/latest-filings";
import { OverviewBlock } from "@/components/overview/overview-block";
import { RecentRecords } from "@/components/overview/recent-records";
import { SystemStatus } from "@/components/overview/system-status";
import { LinkButton } from "@/components/ui/button";
import { Frame } from "@/components/ui/frame";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { Section } from "@/components/ui/section";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";

/**
 * Overview: a launchpad into the research workflow, not a dashboard. It holds no figure of its
 * own: no count, score, aggregate or performance number is computed or invented here. Each block
 * below is one independent list read (or the API's own health), in its own boundary, so one
 * source being slow, empty or down never blanks another. Nothing is polled.
 */
export default function Overview() {
  return (
    <PageContainer width="workspace">
      <PageHeader
        eyebrow="QuantForge"
        title="Overview"
        description="Where to start. Everything on this page is read from the QuantForge API; nothing here is estimated or computed."
      />

      <Frame title="Start research">
        <p className="type-body max-w-3xl text-text-2">
          A study starts from one stored earnings event: the descriptive returns around it, the separate point-in-time verdict, an optional
          benchmark and an optional filing you supply, with every refusal stated. Open Research to find an event, or start from one of the
          latest below.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <LinkButton href="/research" variant="primary">
            Open Research
          </LinkButton>
        </div>
      </Frame>

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
        <OverviewBlock
          title="Latest earnings events"
          note="Newest event date first; scheduled events included."
          loadingLabel="Loading the latest earnings events"
        >
          <LatestEarnings />
        </OverviewBlock>
        <OverviewBlock
          title="Latest filings"
          note="Newest filing date first; filings with SEC FSDS data only."
          loadingLabel="Loading the latest filings"
        >
          <LatestFilings />
        </OverviewBlock>
      </div>

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
        <OverviewBlock
          title="Recent saved research"
          note="Newest saved first. Each is an immutable stored baseline."
          loadingLabel="Loading recent saved research records"
        >
          <RecentRecords />
        </OverviewBlock>
        <Section title="System" note="As reported by the API when this page was served.">
          <Suspense
            fallback={
              <SkeletonRegion label="Loading system status">
                <Skeleton height="9rem" />
              </SkeletonRegion>
            }
          >
            <SystemStatus />
          </Suspense>
        </Section>
      </div>
    </PageContainer>
  );
}
