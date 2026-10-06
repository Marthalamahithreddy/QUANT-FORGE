import { ApiStatus } from "@/components/layout/api-status";
import { getRequestBackendStatus } from "@/lib/api/health";

/**
 * The status chip with its real answer. An async Server Component: the layout wraps it in
 * `Suspense` (fallback: "Checking API"), so the health read streams in after the shell has
 * painted. The read never rejects; an unreachable API renders "Unavailable".
 *
 * The chip reflects the API at the moment the document was rendered. The layout persists across
 * client navigations (that is what keeps the shell from remounting), so it is refreshed by a
 * reload, not on every route change; each page states its own data failures itself.
 */
export async function LiveApiStatus() {
  return <ApiStatus status={await getRequestBackendStatus()} />;
}
