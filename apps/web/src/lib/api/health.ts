/**
 * Backend health integration.
 *
 * The status is resolved on the server during render, so no client-side state
 * or polling is involved. Failures are returned as data rather than thrown, so
 * the shell always renders.
 */

import { apiGet, ApiError, type ApiGetOptions } from "@/lib/api/client";
import { env } from "@/lib/config/env";
import {
  HealthContractError,
  parseHealthResponse,
  type BackendStatus,
  type HealthResponse,
} from "@/types/api";

export type { BackendStatus, HealthResponse } from "@/types/api";

/** Fetches and validates the backend health payload. Throws on any failure. */
export async function fetchBackendHealth(
  options: ApiGetOptions = {},
): Promise<HealthResponse> {
  const payload = await apiGet<unknown>(env.apiHealthPath, options);
  return parseHealthResponse(payload);
}

function describeFailure(error: unknown): string {
  if (error instanceof ApiError) {
    return error.publicMessage;
  }
  if (error instanceof HealthContractError) {
    return error.message; // names the expected shape, never an address
  }
  // Anything else may carry a host or a driver message: say only what is safe.
  return "Unexpected error while contacting the QuantForge API.";
}

/**
 * Resolves the backend status for display.
 *
 * Never rejects: an unreachable or misbehaving backend becomes an
 * `unavailable` state instead of an error boundary.
 */
export async function getBackendStatus(
  options: ApiGetOptions = {},
): Promise<BackendStatus> {
  try {
    const health = await fetchBackendHealth(options);
    return { state: "connected", health };
  } catch (error) {
    return { state: "unavailable", detail: describeFailure(error) };
  }
}
