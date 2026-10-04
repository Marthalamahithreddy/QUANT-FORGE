import { redactUrls } from "@/lib/safe-text";
import type { BackendStatus } from "@/types/api";

export interface ApiStatusProps {
  /** `undefined` while the shell is painted before the API has answered ("checking"). */
  readonly status?: BackendStatus;
}

/**
 * The real backend state, as text. A coloured dot is decorative; the state is always also words
 * (and the service, version and environment, or the safe reason, for assistive technology).
 * "Checking" is shown only while no answer exists: it never claims connected.
 */
export function ApiStatus({ status }: ApiStatusProps) {
  const checking = status === undefined;
  const connected = status?.state === "connected";
  const label = checking ? "Checking API" : connected ? "Connected" : "Unavailable";
  const dot = checking ? "bg-text-3" : connected ? "bg-success" : "bg-danger";
  const description = checking
    ? "Waiting for the QuantForge API to answer."
    : connected
      ? `${status.health.service} ${status.health.version} (${status.health.environment})`
      : redactUrls((status as Extract<BackendStatus, { state: "unavailable" }>).detail);

  const environment = connected && status.health.environment !== "production" ? status.health.environment : null;

  return (
    <div className="flex items-center gap-2">
      {environment ? (
        <span className="type-id hidden rounded-md border border-border-subtle bg-surface-elevated px-2 py-1 text-text-2 sm:inline">
          env: {environment}
        </span>
      ) : null}
      <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-surface-elevated px-2.5 py-1.5">
        <span aria-hidden="true" className={`size-2 rounded-full ${dot}`} />
        <span className={`text-xs font-medium ${checking || !connected ? "text-text-2" : "text-text"}`}>{label}</span>
        <span className="sr-only">{description}</span>
      </div>
    </div>
  );
}
