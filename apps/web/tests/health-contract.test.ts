import { describe, expect, it } from "vitest";

import {
  HealthContractError,
  isHealthResponse,
  parseHealthResponse,
} from "@/types/api";

const VALID = {
  status: "ok",
  service: "quantforge-api",
  version: "0.1.0",
  environment: "local",
} as const;

describe("isHealthResponse", () => {
  it("accepts the documented payload", () => {
    expect(isHealthResponse(VALID)).toBe(true);
  });

  it("accepts a payload carrying additional fields", () => {
    expect(isHealthResponse({ ...VALID, uptimeSeconds: 12 })).toBe(true);
  });

  it.each([
    ["null", null],
    ["undefined", undefined],
    ["a string", "ok"],
    ["a number", 42],
    ["an array", [VALID]],
    ["an empty object", {}],
  ])("rejects %s", (_label, value) => {
    expect(isHealthResponse(value)).toBe(false);
  });

  it.each([
    ["status", { ...VALID, status: "degraded" }],
    ["service", { ...VALID, service: 1 }],
    ["version", { ...VALID, version: null }],
    ["environment", { ...VALID, environment: "" }],
  ])("rejects a payload with a bad %s field", (_label, value) => {
    expect(isHealthResponse(value)).toBe(false);
  });
});

describe("parseHealthResponse", () => {
  it("returns the narrowed payload unchanged", () => {
    expect(parseHealthResponse(VALID)).toEqual(VALID);
  });

  it("throws a typed error on contract drift", () => {
    expect(() => parseHealthResponse({ status: "ok" })).toThrow(
      HealthContractError,
    );
  });

  it("explains what it expected", () => {
    expect(() => parseHealthResponse(null)).toThrow(/status: 'ok'/);
  });
});
