import { describe, expect, it } from "vitest";

import { ContractError } from "@/lib/api/decode";
import { decodeHealth, decodeReadiness } from "@/lib/api/decoders/health";

const VALID = { status: "ok", service: "quantforge-api", version: "0.1.0", environment: "local" } as const;
const health = (value: unknown) => decodeHealth(value, "$");

describe("decodeHealth", () => {
  it("accepts the documented payload", () => {
    expect(health(VALID)).toEqual(VALID);
  });

  it("ignores unknown extra fields so additive backend changes do not break the client", () => {
    expect(health({ ...VALID, uptime_seconds: 12 })).toEqual(VALID);
  });

  it.each([
    ["status other than ok", { ...VALID, status: "degraded" }],
    ["a blank service", { ...VALID, service: "  " }],
    ["a missing version", Object.fromEntries(Object.entries(VALID).filter(([key]) => key !== "version"))],
    ["a non-string environment", { ...VALID, environment: 1 }],
    ["an array", [VALID]],
    ["null", null],
    ["a string", "ok"],
  ])("rejects %s", (_name, value) => {
    expect(() => health(value)).toThrow(ContractError);
  });
});

describe("decodeReadiness", () => {
  const READY = {
    status: "ready",
    service: "s",
    dependencies: { database: { state: "ok", gating: true }, redis: { state: "ok", gating: false } },
    schema: { state: "compatible", expected: "0014", current: "0014", gating: true },
  };
  const withDependencies = (patch: Record<string, unknown>) => ({
    ...READY,
    dependencies: { ...READY.dependencies, ...patch },
  });

  it("treats the optional detail as optional, but not as null-or-absent interchangeably elsewhere", () => {
    expect(decodeReadiness(READY, "$")).toEqual(READY);
    expect(decodeReadiness({ ...READY, detail: null }, "$").detail).toBeNull();
  });

  it.each([
    ["redis unavailable", withDependencies({ redis: { state: "unavailable", gating: false } })],
    ["redis not configured", withDependencies({ redis: { state: "not_configured", gating: false } })],
    ["a schema mismatch", { ...READY, status: "not_ready", schema: { state: "mismatch", expected: "0014", current: "0013", gating: true } }],
    ["an unreadable schema", { ...READY, status: "not_ready", schema: { state: "unknown", expected: "0014", current: null, gating: true } }],
    ["an unknown expected head", { ...READY, status: "not_ready", schema: { state: "unknown", expected: null, current: null, gating: true } }],
  ])("accepts %s", (_name, value) => {
    expect(() => decodeReadiness(value, "$")).not.toThrow();
  });

  it.each([
    ["an unknown readiness status", { ...READY, status: "warming" }],
    ["the old flat dependency shape", { ...READY, dependencies: { database: "ok", redis: "ok" } }],
    ["an unknown database state", withDependencies({ database: { state: "slow", gating: true } })],
    ["an unknown redis state", withDependencies({ redis: { state: "degraded", gating: false } })],
    ["redis claiming to gate", withDependencies({ redis: { state: "ok", gating: true } })],
    ["the database claiming not to gate", withDependencies({ database: { state: "ok", gating: false } })],
    ["a missing dependency", { ...READY, dependencies: { database: READY.dependencies.database } }],
    ["a missing schema block", Object.fromEntries(Object.entries(READY).filter(([key]) => key !== "schema"))],
    ["an unknown schema state", { ...READY, schema: { ...READY.schema, state: "behind" } }],
    ["a non-string revision", { ...READY, schema: { ...READY.schema, current: 14 } }],
    ["the schema claiming not to gate", { ...READY, schema: { ...READY.schema, gating: false } }],
  ])("rejects %s", (_name, value) => {
    expect(() => decodeReadiness(value, "$")).toThrow(ContractError);
  });
});
