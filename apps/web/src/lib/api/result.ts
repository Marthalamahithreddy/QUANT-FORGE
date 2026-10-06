/**
 * The one result type of every API read.
 *
 * Transport and contract outcomes live here; DOMAIN statuses do not. A successful HTTP 200 whose
 * body says REFUSED, AMBIGUOUS, MISSING, PARTIAL or NOT_REQUESTED is `{ kind: "ok" }` carrying
 * that status as data: it is a statement about the domain, not a failed request, and must never be
 * turned into one of the error kinds below.
 *
 * No variant carries a URL, host, port, stack, SQL or raw response text, so any of them can be
 * shown to a reader as is.
 */

/** One rejected request parameter, as the API described it (sanitised). */
export interface ApiIssue {
  /** Where: `query.ticker`, `path.event_id`... */
  readonly where: string;
  readonly message: string;
}

export type ApiResult<T> =
  | { readonly kind: "ok"; readonly data: T }
  /** HTTP 404: nothing is stored under this identifier. */
  | { readonly kind: "not_found" }
  /**
   * HTTP 409: the request is well formed but conflicts with stored state. It carries no response
   * text: a slice that needs the backend's fixed sentence (record verification) allow-lists it.
   */
  | { readonly kind: "conflict" }
  /** HTTP 422: the API rejected the request parameters. */
  | { readonly kind: "rejected"; readonly issues: readonly ApiIssue[] }
  /** HTTP 503: a dependency (the database) is down. Never "no data". */
  | { readonly kind: "unavailable" }
  /** HTTP 5xx other than 503, or any status the contract does not define. */
  | { readonly kind: "server_error"; readonly status: number }
  /** No response at all: refused, DNS, or the request timed out. */
  | { readonly kind: "unreachable"; readonly timedOut: boolean }
  /** A response arrived but does not match the contract this app supports. */
  | { readonly kind: "contract"; readonly path: string; readonly expected: string };

export type ApiFailure = Exclude<ApiResult<unknown>, { kind: "ok" }>;

export const isOk = <T>(result: ApiResult<T>): result is { kind: "ok"; data: T } => result.kind === "ok";
