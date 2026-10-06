import type { ApiResult } from "@/lib/api/result";

/**
 * A response must describe the request that was made. When the request's own identity or paging is
 * echoed back differently, the payload is not the answer to this question, so it is a contract
 * failure rather than something to display.
 */
export function requireEcho<T>(
  result: ApiResult<T>,
  check: (data: T) => { readonly path: string; readonly expected: string } | null,
): ApiResult<T> {
  if (result.kind !== "ok") return result;
  const mismatch = check(result.data);
  return mismatch === null ? result : { kind: "contract", ...mismatch };
}
