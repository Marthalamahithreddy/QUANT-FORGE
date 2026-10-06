/**
 * Backend health integration.
 *
 * Resolved on the server during render, so no client state or polling is involved. A failure is
 * returned as data, never thrown, so the shell always renders.
 */

import { cache } from "react";

import { apiRequest, type ApiRequestOptions } from "@/lib/api/client";
import { decodeHealth } from "@/lib/api/decoders/health";
import { failureSummary } from "@/lib/api/messages";
import type { HealthResponse } from "@/lib/api/schema";
import { env } from "@/lib/config/env";

export type { HealthResponse } from "@/lib/api/schema";

/**
 * Outcome of contacting the backend. A discriminated union, so the UI can never render a
 * fabricated "connected" state: there is no connected variant without a validated payload.
 */
export type BackendStatus =
  | { readonly state: "connected"; readonly health: HealthResponse }
  | { readonly state: "unavailable"; readonly detail: string };

async function resolveStatus(options: ApiRequestOptions): Promise<BackendStatus> {
  const result = await apiRequest(env.apiHealthPath, decodeHealth, options);
  return result.kind === "ok"
    ? { state: "connected", health: result.data }
    : { state: "unavailable", detail: failureSummary(result) };
}

/** Never rejects: an unreachable or misbehaving backend becomes `unavailable`. */
export function getBackendStatus(options: ApiRequestOptions = {}): Promise<BackendStatus> {
  return resolveStatus(options);
}

/**
 * The same read, shared by everything rendered in one request (the layout's status chip and the
 * Overview page both ask), so a page view costs one health call, not two.
 */
export const getRequestBackendStatus = cache(() => resolveStatus({}));
