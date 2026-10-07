import Link from "next/link";
import type { ComponentProps } from "react";

/**
 * Every in-app link except the primary navigation. It never prefetches.
 *
 * In production, `<Link>` prefetches whatever enters the viewport, and a prefetch of a dynamic
 * route RENDERS it on the server. Here a rendered page reads the QuantForge API, so one list of N
 * rows would cause an extra read per link (measured: an Overview of five events made fifteen API
 * requests, a saved-research list twenty-three), and a prefetch of a `?verify=1` address could
 * start a verification nobody asked for. Navigation is read on demand instead: the API is asked
 * when the reader follows a link, and only then.
 *
 * The primary navigation keeps the default (`nav-link.tsx`): its targets read nothing outside
 * their own Suspense boundaries, so prefetching them costs no API request.
 */
export function AppLink(props: Omit<ComponentProps<typeof Link>, "prefetch">) {
  // After the spread, and absent from the props type: no caller can switch prefetching back on.
  return <Link {...props} prefetch={false} />;
}
