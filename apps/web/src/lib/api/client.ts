/**
 * Minimal typed wrapper around the platform `fetch`.
 *
 * Deliberately small: no retry, no cache, no interceptors. Those belong to the
 * domain layer once real endpoints exist.
 */

import { env } from "@/lib/config/env";

/** Short enough that a dead backend surfaces quickly in the UI. */
export const DEFAULT_REQUEST_TIMEOUT_MS = 4000;

/** How a request failed, decided at the throw site (never inferred from message text). */
export type ApiErrorKind = "timeout" | "unreachable" | "http" | "invalid_json";

/** A user-visible description of a failed request that never contains the address. */
function publicMessageFor(kind: ApiErrorKind, status: number): string {
  switch (kind) {
    case "timeout":
      return "The QuantForge API did not answer in time.";
    case "unreachable":
      return "The QuantForge API could not be reached.";
    case "invalid_json":
      return "The QuantForge API returned a response that was not valid JSON.";
    case "http":
      return `The QuantForge API answered HTTP ${status}.`;
  }
}

export class ApiError extends Error {
  readonly url: string;

  /** HTTP status, or `0` when the request never produced a response. */
  readonly status: number;

  readonly kind: ApiErrorKind;

  /**
   * What a reader may be shown: no address, no host, no response text. The full `message` (which
   * names the URL) is for logs and tests only.
   */
  readonly publicMessage: string;

  constructor(
    message: string,
    options: { url: string; status: number; kind: ApiErrorKind; cause?: unknown },
  ) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.url = options.url;
    this.status = options.status;
    this.kind = options.kind;
    this.publicMessage = publicMessageFor(options.kind, options.status);
  }
}

export interface ApiGetOptions {
  readonly baseUrl?: string;
  readonly timeoutMs?: number;
  readonly headers?: Readonly<Record<string, string>>;
  readonly signal?: AbortSignal;
  /**
   * Non-2xx statuses whose JSON body is still a documented contract (e.g. the
   * readiness probe answers 503 with a structured body). Every other non-2xx
   * status throws.
   */
  readonly acceptStatuses?: readonly number[];
}

/** Joins a base URL and a path with exactly one separating slash. */
export function buildUrl(baseUrl: string, path: string): string {
  const base = baseUrl.replace(/\/+$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

function describeTransportFailure(cause: unknown, timeoutMs: number): string {
  if (cause instanceof DOMException && cause.name === "TimeoutError") {
    return `timed out after ${timeoutMs}ms`;
  }
  if (cause instanceof Error) {
    return cause.message;
  }
  return "no response from the server";
}

/**
 * Performs a typed GET.
 *
 * @throws {ApiError} on transport failure, timeout, non-2xx status, or an
 * unparseable body. The raw payload is returned as `unknown` and must be
 * validated by the caller, so an unchecked cast never reaches the UI.
 */
export async function apiGet<TResponse = unknown>(
  path: string,
  options: ApiGetOptions = {},
): Promise<TResponse> {
  const {
    baseUrl = env.apiBaseUrl,
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    headers,
    signal,
    acceptStatuses = [],
  } = options;

  const url = buildUrl(baseUrl, path);
  const timeoutSignal = AbortSignal.timeout(timeoutMs);

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: { accept: "application/json", ...headers },
      // Health must reflect the backend as it is now, never a cached answer.
      cache: "no-store",
      signal: signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal,
    });
  } catch (cause) {
    throw new ApiError(
      `Cannot reach ${url}: ${describeTransportFailure(cause, timeoutMs)}`,
      {
        url,
        status: 0,
        kind: cause instanceof DOMException && cause.name === "TimeoutError" ? "timeout" : "unreachable",
        cause,
      },
    );
  }

  if (!response.ok && !acceptStatuses.includes(response.status)) {
    throw new ApiError(
      `Request to ${url} failed with HTTP ${response.status} ${response.statusText}`.trim(),
      { url, status: response.status, kind: "http" },
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch (cause) {
    throw new ApiError(`Response from ${url} was not valid JSON.`, {
      url,
      status: response.status,
      kind: "invalid_json",
      cause,
    });
  }

  return payload as TResponse;
}
