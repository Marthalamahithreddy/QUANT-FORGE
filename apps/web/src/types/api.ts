/**
 * Wire contracts mirrored from the FastAPI backend.
 *
 * The `HealthResponse` shape must stay in sync with
 * `quantforge.api.v1.health.HealthResponse` on the API side.
 */

export interface HealthResponse {
  readonly status: "ok";
  readonly service: string;
  readonly version: string;
  readonly environment: string;
}

/**
 * Outcome of contacting the backend.
 *
 * Modelled as a discriminated union so the UI can never render a fabricated
 * "connected" state: there is no connected variant without a validated payload.
 */
export type BackendStatus =
  | { readonly state: "connected"; readonly health: HealthResponse }
  | { readonly state: "unavailable"; readonly detail: string };

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value.trim() !== "";

/**
 * Narrows an unknown payload to {@link HealthResponse}.
 *
 * Requires the four documented fields with the expected types. Unknown extra
 * fields are ignored so that additive backend changes do not break the client.
 */
export function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  return (
    candidate.status === "ok" &&
    isNonEmptyString(candidate.service) &&
    isNonEmptyString(candidate.version) &&
    isNonEmptyString(candidate.environment)
  );
}

/** Thrown when a response arrives but does not match the health contract. */
export class HealthContractError extends Error {
  constructor(reason: string) {
    super(`Unexpected payload from the QuantForge health endpoint: ${reason}`);
    this.name = "HealthContractError";
  }
}

/** Validates and narrows an unknown payload, throwing on contract drift. */
export function parseHealthResponse(value: unknown): HealthResponse {
  if (!isHealthResponse(value)) {
    throw new HealthContractError(
      "expected { status: 'ok', service, version, environment }.",
    );
  }
  return value;
}
