import type { BackendStatus } from "@/types/api";

export interface BackendStatusBadgeProps {
  readonly status: BackendStatus;
  /** `compact` is used in the top bar; `block` renders a fuller panel. */
  readonly variant?: "compact" | "block";
}

function statusAppearance(status: BackendStatus): {
  label: string;
  dotClassName: string;
  textClassName: string;
} {
  if (status.state === "connected") {
    return {
      label: "Connected",
      dotClassName: "bg-status-ok",
      textClassName: "text-text-primary",
    };
  }
  return {
    label: "Unavailable",
    dotClassName: "bg-status-down",
    textClassName: "text-text-secondary",
  };
}

/**
 * Renders the real backend state.
 *
 * A coloured dot is decorative only; the state is also conveyed in text so it
 * is available to assistive technology and to users who cannot distinguish the
 * status colours.
 */
export function BackendStatusBadge({
  status,
  variant = "compact",
}: BackendStatusBadgeProps) {
  const { label, dotClassName, textClassName } = statusAppearance(status);
  const description =
    status.state === "connected"
      ? `${status.health.service} ${status.health.version} (${status.health.environment})`
      : status.detail;

  if (variant === "block") {
    return (
      <div className="rounded-md border border-border-subtle bg-surface-1 p-4">
        <div className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={`size-2 rounded-full ${dotClassName}`}
          />
          <span className={`text-sm font-medium ${textClassName}`}>{label}</span>
        </div>
        <p className="mt-2 font-mono text-xs break-words text-text-muted">
          {description}
        </p>
      </div>
    );
  }

  return (
    <div
      className="flex items-center gap-2 rounded-md border border-border-subtle bg-surface-2 px-2.5 py-1.5"
    >
      <span aria-hidden="true" className={`size-2 rounded-full ${dotClassName}`} />
      <span className={`text-xs font-medium ${textClassName}`}>{label}</span>
      <span className="sr-only">{description}</span>
    </div>
  );
}

export interface TopbarProps {
  readonly status: BackendStatus;
}

/** Application chrome: product identity on the left, backend state on the right. */
export function Topbar({ status }: TopbarProps) {
  const environment =
    status.state === "connected" ? status.health.environment : "local";

  return (
    <header className="border-b border-border-subtle bg-surface-1">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-baseline gap-3">
          <span className="text-sm font-semibold tracking-tight text-text-primary">
            QuantForge
          </span>
          <span className="hidden text-xs text-text-muted sm:inline">
            Event-driven equity research
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span className="rounded-md border border-border-subtle bg-surface-2 px-2.5 py-1.5 font-mono text-xs text-text-secondary">
            env: {environment}
          </span>
          <BackendStatusBadge status={status} />
        </div>
      </div>
    </header>
  );
}
