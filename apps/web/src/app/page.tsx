import Link from "next/link";
import type { ReactNode } from "react";

import { StatusBadge } from "@/components/state/status-badge";
import { Frame } from "@/components/ui/frame";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { getEarningsPage, getReadiness } from "@/lib/api/earnings";
import { getRequestBackendStatus } from "@/lib/api/health";
import { failureSummary } from "@/lib/api/messages";
import type { DependencyState } from "@/lib/api/schema";
import { MISSING } from "@/lib/format";

function Line({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border-subtle py-2 last:border-b-0">
      <dt className="text-sm text-text-2">{label}</dt>
      <dd className="num text-sm text-text">{children}</dd>
    </div>
  );
}

const dependencyBadge = (state: DependencyState) => (
  <StatusBadge kind={state === "ok" ? "VALUE" : "ERROR"}>{state === "ok" ? "Reachable" : "Unavailable"}</StatusBadge>
);

export default async function Overview() {
  // Resolved once per request on the server, in parallel. The health read is shared with the
  // shell's status chip (one call per page view). Never polled, never faked.
  const [status, readiness, earnings] = await Promise.all([
    getRequestBackendStatus(),
    getReadiness(),
    getEarningsPage({ limit: 1 }),
  ]);

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Research workspace"
        title="Overview"
        description="System state and data coverage, read from the QuantForge API. Nothing on this page is estimated."
      />

      <div className="grid gap-4 md:grid-cols-2">
        <Frame title="System">
          <dl>
            <Line label="API process">
              {status.state === "connected" ? (
                <StatusBadge kind="VALUE">Live · {status.health.version}</StatusBadge>
              ) : (
                <StatusBadge kind="ERROR">Unavailable</StatusBadge>
              )}
            </Line>
            <Line label="Database">
              {readiness.kind === "ok" ? dependencyBadge(readiness.data.dependencies.database) : MISSING}
            </Line>
            <Line label="Redis">
              {readiness.kind === "ok" ? dependencyBadge(readiness.data.dependencies.redis) : MISSING}
            </Line>
          </dl>
          {status.state === "unavailable" ? (
            <p role="alert" className="type-meta mt-3 break-words">
              {status.detail}
            </p>
          ) : null}
          {readiness.kind !== "ok" ? (
            <p className="type-meta mt-3">Dependency readiness could not be read: {failureSummary(readiness)}</p>
          ) : null}
        </Frame>

        <Frame title="Earnings data">
          {earnings.kind !== "ok" ? (
            <p className="text-sm text-text-2">Earnings coverage is unavailable because the API did not answer.</p>
          ) : earnings.data.total === 0 ? (
            <p className="text-sm text-text-2">
              No earnings events are stored yet. Coverage appears here once ingestion has loaded events.
            </p>
          ) : (
            <dl>
              <Line label="Earnings observations stored">{earnings.data.total}</Line>
              <Line label="Latest event date (incl. scheduled)">{earnings.data.items[0]?.event_date ?? MISSING}</Line>
            </dl>
          )}
          <p className="mt-3 text-sm">
            <Link href="/earnings" className="text-accent hover:underline">
              Open the earnings list →
            </Link>
          </p>
        </Frame>
      </div>

      <Frame title="What is available">
        <ul className="flex flex-col gap-2 text-sm text-text-2">
          <li>
            <span className="text-text">Earnings</span> — stored events with reported and estimated EPS, availability
            evidence and the backend&apos;s point-in-time classification.
          </li>
          <li>
            <span className="text-text">Filings</span> — stored filings with SEC FSDS data, and their descriptive EPS.
          </li>
          <li>
            <span className="text-text">Research</span> — how the research engine behaves. The event-study workspace is
            not part of the interface yet, although the API serves it.
          </li>
          <li>
            <span className="text-text">Saved research</span> — stored research baselines. Nothing can be saved from the
            interface, and none are shown yet.
          </li>
          <li>
            <span className="text-text-3">Not available yet</span> — Companies and Backtests.
          </li>
        </ul>
      </Frame>
    </PageContainer>
  );
}
