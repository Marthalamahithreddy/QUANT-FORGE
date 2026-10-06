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
  const READY = { status: "ready", service: "s", dependencies: { database: "ok", redis: "ok" } };

  it("treats the optional detail as optional, but not as null-or-absent interchangeably elsewhere", () => {
    expect(decodeReadiness(READY, "$")).toEqual(READY);
    expect(decodeReadiness({ ...READY, detail: null }, "$").detail).toBeNull();
  });

  it.each([
    ["an unknown readiness status", { ...READY, status: "warming" }],
    ["an unknown dependency state", { ...READY, dependencies: { database: "slow", redis: "ok" } }],
    ["a missing dependency", { ...READY, dependencies: { database: "ok" } }],
  ])("rejects %s", (_name, value) => {
    expect(() => decodeReadiness(value, "$")).toThrow(ContractError);
  });
});
