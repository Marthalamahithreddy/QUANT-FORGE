import Link from "next/link";
import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { getEarningsPage, getReadiness } from "@/lib/api/earnings";
import { getBackendStatus } from "@/lib/api/health";
import type { Tone } from "@/lib/availability";
import { MISSING } from "@/lib/format";
import type { DependencyState } from "@/types/earnings";

function Panel({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <section aria-label={title} className="rounded-md border border-border-subtle bg-surface-1">
      <h2 className="border-b border-border-subtle px-4 py-2 text-[11px] font-medium tracking-[0.14em] text-text-muted uppercase">
        {title}
      </h2>
      <div className="p-4">{children}</div>
    </section>
  );
}

function Line({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border-subtle py-2 last:border-b-0">
      <dt className="text-sm text-text-secondary">{label}</dt>
      <dd className="font-tabular text-sm text-text-primary">{children}</dd>
    </div>
  );
}

const dependencyBadge = (state: DependencyState) => {
  const tone: Tone = state === "ok" ? "positive" : "negative";
  return <Badge tone={tone}>{state === "ok" ? "Reachable" : "Unavailable"}</Badge>;
};

export default async function Overview() {
  // Resolved once per request on the server. Never polled, never faked.
  const [status, readiness, earnings] = await Promise.all([
    getBackendStatus(),
    getReadiness(),
    getEarningsPage({ limit: 1 }),
  ]);

  return (
    <AppShell status={status}>
      <div className="mx-auto flex max-w-5xl flex-col gap-6">
        <PageHeader
          eyebrow="Research workspace"
          title="Overview"
          description="System state and data coverage, read from the QuantForge API. Nothing on this page is estimated."
        />

        <div className="grid gap-4 md:grid-cols-2">
          <Panel title="System">
            <dl>
              <Line label="API process">
                {status.state === "connected" ? (
                  <Badge tone="positive">Live · {status.health.version}</Badge>
                ) : (
                  <Badge tone="negative">Unavailable</Badge>
                )}
              </Line>
              <Line label="Database">
                {readiness.state === "ok" ? dependencyBadge(readiness.data.dependencies.database) : MISSING}
              </Line>
              <Line label="Redis">
                {readiness.state === "ok" ? dependencyBadge(readiness.data.dependencies.redis) : MISSING}
              </Line>
            </dl>
            {status.state === "unavailable" ? (
              <p role="alert" className="mt-3 font-mono text-xs break-words text-text-muted">
                {status.detail}
              </p>
            ) : null}
            {readiness.state === "error" ? (
              <p className="mt-3 text-xs text-text-muted">
                Dependency readiness could not be read: {readiness.detail}
              </p>
            ) : null}
          </Panel>

          <Panel title="Earnings data">
            {earnings.state !== "ok" ? (
              <p className="text-sm text-text-secondary">
                Earnings coverage is unavailable because the API did not answer.
              </p>
            ) : earnings.data.total === 0 ? (
              <p className="text-sm text-text-secondary">
                No earnings events are stored yet. Coverage appears here once ingestion has
                loaded events.
              </p>
            ) : (
              <dl>
                <Line label="Earnings observations stored">{earnings.data.total}</Line>
                <Line label="Latest event date (incl. scheduled)">
                  {earnings.data.items[0]?.event_date ?? MISSING}
                </Line>
              </dl>
            )}
            <p className="mt-3 text-sm">
              <Link href="/earnings" className="text-accent hover:underline">
                Open the earnings explorer →
              </Link>
            </p>
          </Panel>
        </div>

        <Panel title="What is available">
          <ul className="flex flex-col gap-2 text-sm text-text-secondary">
            <li>
              <span className="text-text-primary">Earnings explorer</span> — stored events with
              reported and estimated EPS, availability evidence and the backend&apos;s
              point-in-time classification.
            </li>
            <li>
              <span className="text-text-primary">Research</span> — a description of the research
              engine. Event-study results are not exposed by the API yet.
            </li>
            <li>
              <span className="text-text-muted">Companies and Backtests</span> — not implemented.
            </li>
          </ul>
        </Panel>
      </div>
    </AppShell>
  );
}
