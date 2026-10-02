/**
 * Public runtime configuration.
 *
 * Every `process.env` read in this application must go through this module, so
 * the full set of externalised variables stays discoverable in one place.
 */

/** Used when `NEXT_PUBLIC_API_BASE_URL` is unset or blank. Local dev only. */
export const DEFAULT_API_BASE_URL = "http://localhost:8000";

/** Path of the versioned health endpoint exposed by the FastAPI service. */
export const API_HEALTH_PATH = "/api/v1/health";

function parseBaseUrl(raw: string | undefined): string {
  const candidate =
    raw === undefined || raw.trim() === "" ? DEFAULT_API_BASE_URL : raw.trim();

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error(
      `NEXT_PUBLIC_API_BASE_URL must be an absolute URL, but received "${candidate}".`,
    );
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(
      `NEXT_PUBLIC_API_BASE_URL must use http or https, but received "${candidate}".`,
    );
  }

  // Trailing slashes would produce "//api/v1/health" once a path is appended.
  return candidate.replace(/\/+$/, "");
}

export interface AppEnv {
  readonly apiBaseUrl: string;
  readonly apiHealthPath: string;
}

export const env: AppEnv = Object.freeze({
  apiBaseUrl: parseBaseUrl(process.env.NEXT_PUBLIC_API_BASE_URL),
  apiHealthPath: API_HEALTH_PATH,
});
