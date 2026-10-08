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
import {
  DATABASE_STATES,
  READINESS_STATUSES,
  REDIS_STATES,
  SCHEMA_STATES,
  type HealthResponse,
  type ReadinessResponse,
} from "@/lib/api/schema";

/** `GET /health` and `GET /api/v1/health`. */
export const decodeHealth = object<HealthResponse>({
  status: literal("ok"),
  service: nonEmptyString,
  version: nonEmptyString,
  environment: nonEmptyString,
});

/**
 * `GET /api/v1/health/ready`: the same shape for 200 and for the documented 503 body.
 *
 * The gating policy is part of the contract and is checked strictly: the database and the schema
 * must say `gating: true` and Redis `gating: false`. A backend that changed which component gates
 * readiness fails here loudly rather than being displayed under the old assumption.
 */
export const decodeReadiness = object<ReadinessResponse>({
  status: oneOf(READINESS_STATUSES, "readiness status"),
  service: nonEmptyString,
  dependencies: object<ReadinessResponse["dependencies"]>({
    database: object({ state: oneOf(DATABASE_STATES, "database state"), gating: literal(true) }),
    redis: object({ state: oneOf(REDIS_STATES, "redis state"), gating: literal(false) }),
  }),
  schema: object<ReadinessResponse["schema"]>({
    state: oneOf(SCHEMA_STATES, "schema state"),
    expected: nullable(string),
    current: nullable(string),
    gating: literal(true),
  }),
  detail: optional(nullable(recordOf(string))),
});
