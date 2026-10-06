/**
 * The single server-side HTTP boundary to the QuantForge API.
 *
 * `apiRequest` performs one GET, maps every outcome onto `ApiResult` (see result.ts) and validates
 * a success body with the caller's decoder, so an unchecked cast never reaches the UI. It does not
 * retry or cache.
 *
 * Browser code never calls the API: only server modules import this file (tests/server-boundary
 * pins the import graph), so the base URL and every request stay on the server.
 *
 * Timeout classes (the API serves bounded, indexed reads; see README "API contract"):
 *
 * - `READ` (4 s, the default): health, readiness, list pages, one event, one filing EPS;
 * - a longer class is reserved for the computed endpoints (research observation, record
 *   verification) and arrives with their clients; no endpoint used today needs it, so none is
 *   given more than 4 s merely for uniformity.
 */

import { decode, type Decoder } from "@/lib/api/decode";
import type { ApiIssue, ApiResult } from "@/lib/api/result";
import { env } from "@/lib/config/env";
import { redactUrls } from "@/lib/safe-text";

export const READ_TIMEOUT_MS = 4000;

export interface ApiRequestOptions {
  readonly baseUrl?: string;
  readonly timeoutMs?: number;
  readonly signal?: AbortSignal;
  /**
   * Non-2xx statuses whose JSON body is a documented success contract (the readiness probe
   * answers 503 with a structured body). Every other non-2xx status maps to its failure kind.
   */
  readonly acceptStatuses?: readonly number[];
  /**
   * Whether a 404 is the API saying "nothing is stored under this identifier" (a read of ONE
   * resource). For a collection, health or readiness read a 404 can only mean a wrong path or a
   * misrouted gateway, which is a service fault, not a domain answer, so it stays `server_error`.
   */
  readonly notFoundMeansAbsent?: boolean;
}

/** Joins a base URL and a path with exactly one separating slash. */
export function buildUrl(baseUrl: string, path: string): string {
  const base = baseUrl.replace(/\/+$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

const MAX_ISSUES = 10;
const MAX_TEXT = 200;

function clip(text: string): string {
  const safe = redactUrls(text);
  return safe.length > MAX_TEXT ? `${safe.slice(0, MAX_TEXT)}…` : safe;
}

/** FastAPI's 422 body: `{ detail: [{ loc, msg, type }] }`. The echoed `input` is never read. */
function issuesFrom(body: unknown): readonly ApiIssue[] {
  if (typeof body !== "object" || body === null || !("detail" in body)) return [];
  const detail = (body as { detail: unknown }).detail;
  if (!Array.isArray(detail)) return [];
  const issues: ApiIssue[] = [];
  for (const entry of detail.slice(0, MAX_ISSUES)) {
    if (typeof entry !== "object" || entry === null) continue;
    const { loc, msg } = entry as { loc?: unknown; msg?: unknown };
    if (typeof msg !== "string") continue;
    const where = Array.isArray(loc)
      ? loc.filter((part): part is string | number => typeof part === "string" || typeof part === "number").join(".")
      : "";
    issues.push({ where: clip(where), message: clip(msg) });
  }
  return issues;
}

async function readJson(response: Response): Promise<{ ok: true; body: unknown } | { ok: false }> {
  try {
    return { ok: true, body: await response.json() };
  } catch {
    return { ok: false };
  }
}

/** One GET, mapped onto `ApiResult`. Never throws for an expected failure. */
export async function apiRequest<T>(
  path: string,
  decoder: Decoder<T>,
  options: ApiRequestOptions = {},
): Promise<ApiResult<T>> {
  const {
    baseUrl = env.apiBaseUrl,
    timeoutMs = READ_TIMEOUT_MS,
    signal,
    acceptStatuses = [],
    notFoundMeansAbsent = false,
  } = options;

  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const combined = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
  let response: Response;
  try {
    response = await fetch(buildUrl(baseUrl, path), {
      method: "GET",
      headers: { accept: "application/json" },
      // Every read reflects the backend as it is now, never a cached answer.
      cache: "no-store",
      signal: combined,
    });
  } catch (cause) {
    const timedOut = cause instanceof DOMException && cause.name === "TimeoutError";
    return { kind: "unreachable", timedOut };
  }

  const { status } = response;
  if (!response.ok && !acceptStatuses.includes(status)) {
    // A failure body is read only for the two statuses whose body is a documented contract.
    if (status === 404 && notFoundMeansAbsent) return { kind: "not_found" };
    if (status === 409) return { kind: "conflict" };
    if (status === 422) {
      const parsed = await readJson(response);
      return { kind: "rejected", issues: issuesFrom(parsed.ok ? parsed.body : undefined) };
    }
    if (status === 503) return { kind: "unavailable" };
    return { kind: "server_error", status };
  }

  const parsed = await readJson(response);
  if (!parsed.ok) {
    // The body stalled or the connection dropped mid-read: that is the transport, not the contract.
    if (combined.aborted) return { kind: "unreachable", timedOut: timeoutSignal.aborted };
    return { kind: "contract", path: "$", expected: "a JSON document" };
  }
  const outcome = decode(decoder, parsed.body);
  return outcome.ok
    ? { kind: "ok", data: outcome.data }
    : { kind: "contract", path: outcome.path, expected: outcome.expected };
}
