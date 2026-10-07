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

/** Path of the versioned readiness endpoint (database and Redis reachability). */
export const API_READINESS_PATH = "/api/v1/health/ready";

/** Path of the read-only earnings-event collection. */
export const API_EARNINGS_PATH = "/api/v1/earnings-events";

/** Path of the read-only filings index; a filing's EPS is `{path}/{accession}/eps`. */
export const API_FILINGS_PATH = "/api/v1/filings";

/** Path of the read-only research observation; one event is `{path}/{event_id}`. */
export const API_OBSERVATIONS_PATH = "/api/v1/research/observations";

/** Path of the read-only saved research records; one is `{path}/{record_id}`, its verification `{path}/{record_id}/verify`. */
export const API_RECORDS_PATH = "/api/v1/research/records";

/** Path of the benchmark index (identity fields only). */
export const API_BENCHMARKS_PATH = "/api/v1/benchmarks";

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
  readonly apiReadinessPath: string;
  readonly apiEarningsPath: string;
  readonly apiFilingsPath: string;
  readonly apiObservationsPath: string;
  readonly apiBenchmarksPath: string;
  readonly apiRecordsPath: string;
}

export const env: AppEnv = Object.freeze({
  apiBaseUrl: parseBaseUrl(process.env.NEXT_PUBLIC_API_BASE_URL),
  apiHealthPath: API_HEALTH_PATH,
  apiReadinessPath: API_READINESS_PATH,
  apiEarningsPath: API_EARNINGS_PATH,
  apiFilingsPath: API_FILINGS_PATH,
  apiObservationsPath: API_OBSERVATIONS_PATH,
  apiBenchmarksPath: API_BENCHMARKS_PATH,
  apiRecordsPath: API_RECORDS_PATH,
});
