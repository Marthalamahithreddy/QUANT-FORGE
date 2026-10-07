import type { ReactNode } from "react";

import { StatusBadge } from "@/components/state/status-badge";
import { getReadiness } from "@/lib/api/earnings";
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

/**
 * What the API itself reports about its process and its two dependencies. The health read is the
 * one the shell's status chip already made (shared per request); readiness is one more. Neither is
 * polled, and an unreadable value is shown as unavailable, never as healthy.
 */
export async function SystemStatus() {
  const [status, readiness] = await Promise.all([getRequestBackendStatus(), getReadiness()]);
  return (
    <>
      <dl>
        <Line label="API process">
          {status.state === "connected" ? (
            <StatusBadge kind="VALUE">Live · {status.health.version}</StatusBadge>
          ) : (
            <StatusBadge kind="ERROR">Unavailable</StatusBadge>
          )}
        </Line>
        <Line label="Database">{readiness.kind === "ok" ? dependencyBadge(readiness.data.dependencies.database) : MISSING}</Line>
        <Line label="Redis">{readiness.kind === "ok" ? dependencyBadge(readiness.data.dependencies.redis) : MISSING}</Line>
      </dl>
      {status.state === "unavailable" ? (
        <p role="alert" className="type-meta mt-3 break-words">
          {status.detail}
        </p>
      ) : null}
      {readiness.kind !== "ok" ? (
        <p className="type-meta mt-3">Dependency readiness could not be read: {failureSummary(readiness)}</p>
      ) : null}
    </>
  );
}
