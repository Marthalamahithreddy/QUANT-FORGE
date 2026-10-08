import type { ReactNode } from "react";

import { StatusBadge } from "@/components/state/status-badge";
import { getReadiness } from "@/lib/api/earnings";
import { getRequestBackendStatus } from "@/lib/api/health";
import { failureSummary } from "@/lib/api/messages";
import type { DatabaseState, RedisState } from "@/lib/api/schema";
import { MISSING } from "@/lib/format";

function Line({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border-subtle py-2 last:border-b-0">
      <dt className="text-sm text-text-2">{label}</dt>
      <dd className="num text-sm text-text">{children}</dd>
    </div>
  );
}

const databaseBadge = (state: DatabaseState) => (
  <StatusBadge kind={state === "ok" ? "VALUE" : "ERROR"}>{state === "ok" ? "Reachable" : "Unavailable"}</StatusBadge>
);

/**
 * Redis never gates readiness: nothing that serves the stored product uses it. So an outage is a
 * plain fact, not an application error, and an unconfigured Redis is not an outage at all.
 */
const redisBadge = (state: RedisState) =>
  state === "ok" ? (
    <StatusBadge kind="VALUE">Reachable</StatusBadge>
  ) : state === "not_configured" ? (
    <StatusBadge kind="NOT_REQUESTED">Not configured</StatusBadge>
  ) : (
    <StatusBadge kind="UNAVAILABLE">Unavailable · not required</StatusBadge>
  );

/**
 * What the API itself reports about its process, its dependencies and whether it is ready to serve.
 * A readiness failure caused by the schema is shown only as "Not ready": migration revisions are an
 * operator's concern and are decoded but never displayed. The health read is the
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
        <Line label="Database">{readiness.kind === "ok" ? databaseBadge(readiness.data.dependencies.database.state) : MISSING}</Line>
        <Line label="Redis">{readiness.kind === "ok" ? redisBadge(readiness.data.dependencies.redis.state) : MISSING}</Line>
        <Line label="Ready to serve">
          {readiness.kind !== "ok" ? (
            MISSING
          ) : readiness.data.status === "ready" ? (
            <StatusBadge kind="VALUE">Ready</StatusBadge>
          ) : (
            <StatusBadge kind="ERROR">Not ready</StatusBadge>
          )}
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
    </>
  );
}
