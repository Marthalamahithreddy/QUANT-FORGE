import type { ApiFailure } from "@/lib/api/result";

/** What a reader may be told about a failed request. Fixed sentences: no address, no raw text. */
export interface FailureCopy {
  readonly title: string;
  readonly body: string;
}

/**
 * The single place the words for an API failure live (spec section 11.3). Every string here is a
 * constant or built from a status number and a field path, with ONE exception: a 422 lists the
 * API's own validation messages about the request's parameters (location and pydantic text, the
 * echoed input dropped, URLs redacted, length-capped). Nothing else from a response body or a
 * thrown exception can reach a reader through it.
 */
export function failureCopy(failure: ApiFailure, development = process.env.NODE_ENV !== "production"): FailureCopy {
  switch (failure.kind) {
    case "not_found":
      return { title: "Not found", body: "Nothing is stored under this identifier." };
    case "conflict":
      return { title: "The request conflicts with stored data", body: "The API could not apply this request to the data it holds." };
    case "rejected":
      return {
        title: "The API rejected these parameters",
        body:
          failure.issues.length === 0
            ? "The API did not accept the request parameters."
            : failure.issues.map((issue) => (issue.where ? `${issue.where}: ${issue.message}` : issue.message)).join(" "),
      };
    case "unavailable":
      return {
        title: "Temporarily unavailable",
        body: "The database cannot be reached right now. This is a service problem, not an empty result.",
      };
    case "server_error":
      return {
        title: "The server hit an unexpected error",
        body: `The QuantForge API answered HTTP ${failure.status}. This is a service problem, not an empty result.`,
      };
    case "unreachable":
      return {
        title: "Cannot reach the QuantForge API",
        body: failure.timedOut
          ? "The request timed out. The service may be slow or not running."
          : "The service is not running or cannot be reached.",
      };
    case "contract":
      return {
        title: "The API returned data this version of the app does not understand",
        body: development
          ? `Contract mismatch at ${failure.path}: expected ${failure.expected}.`
          : "The response did not match the contract this app supports, so nothing from it is shown.",
      };
  }
}

/** One short line for the status chip and other tight spaces. */
export function failureSummary(failure: ApiFailure): string {
  switch (failure.kind) {
    case "unreachable":
      return failure.timedOut ? "The QuantForge API did not answer in time." : "The QuantForge API could not be reached.";
    case "unavailable":
      return "The QuantForge API reports its database is unavailable.";
    case "server_error":
      return `The QuantForge API answered HTTP ${failure.status}.`;
    case "contract":
      return "The QuantForge API returned a response this app does not understand.";
    case "not_found":
      return "The requested resource was not found.";
    case "rejected":
      return "The QuantForge API rejected the request.";
    case "conflict":
      return "The request conflicts with stored data.";
  }
}
