import {
  literal,
  nonEmptyString,
  nullable,
  object,
  oneOf,
  optional,
  recordOf,
  string,
} from "@/lib/api/decode";
import { DEPENDENCY_STATES, READINESS_STATUSES, type HealthResponse, type ReadinessResponse } from "@/lib/api/schema";

/** `GET /health` and `GET /api/v1/health`. */
export const decodeHealth = object<HealthResponse>({
  status: literal("ok"),
  service: nonEmptyString,
  version: nonEmptyString,
  environment: nonEmptyString,
});

const dependency = oneOf(DEPENDENCY_STATES, "dependency state");

/** `GET /api/v1/health/ready`: the same shape for 200 and for the documented 503 body. */
export const decodeReadiness = object<ReadinessResponse>({
  status: oneOf(READINESS_STATUSES, "readiness status"),
  service: nonEmptyString,
  dependencies: object<ReadinessResponse["dependencies"]>({ database: dependency, redis: dependency }),
  detail: optional(nullable(recordOf(string))),
});
