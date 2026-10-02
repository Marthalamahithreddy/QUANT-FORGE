/**
 * Minimal typed wrapper around the platform `fetch`.
 *
 * Deliberately small: no retry, no cache, no interceptors. Those belong to the
 * domain layer once real endpoints exist.
 */

import { env } from "@/lib/config/env";

/** Short enough that a dead backend surfaces quickly in the UI. */
export const DEFAULT_REQUEST_TIMEOUT_MS = 4000;

export class ApiError extends Error {
  readonly url: string;

  /** HTTP status, or `0` when the request never produced a response. */
  readonly status: number;

  constructor(
    message: string,
    options: { url: string; status: number; cause?: unknown },
  ) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.url = options.url;
    this.status = options.status;
  }
}

export interface ApiGetOptions {
  readonly baseUrl?: string;
  readonly timeoutMs?: number;
  readonly headers?: Readonly<Record<string, string>>;
  readonly signal?: AbortSignal;
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
      { url, status: 0, cause },
    );
  }

  if (!response.ok) {
    throw new ApiError(
      `Request to ${url} failed with HTTP ${response.status} ${response.statusText}`.trim(),
      { url, status: response.status },
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch (cause) {
    throw new ApiError(`Response from ${url} was not valid JSON.`, {
      url,
      status: response.status,
      cause,
    });
  }

  return payload as TResponse;
}
