import { describe, expect, it } from "vitest";

import { API_HEALTH_PATH, DEFAULT_API_BASE_URL, env } from "@/lib/config/env";
import { NAV_ITEMS } from "@/lib/navigation";

describe("env", () => {
  it("exposes the documented local development base URL", () => {
    expect(env.apiBaseUrl).toBe(DEFAULT_API_BASE_URL);
    expect(env.apiBaseUrl).toBe("http://localhost:8000");
  });

  it("points at the versioned health endpoint", () => {
    expect(env.apiHealthPath).toBe(API_HEALTH_PATH);
    expect(env.apiHealthPath).toBe("/api/v1/health");
  });

  it("has no trailing slash, so paths never double up", () => {
    expect(env.apiBaseUrl.endsWith("/")).toBe(false);
  });
});

describe("navigation model", () => {
  it("declares the six planned workspace sections", () => {
    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      "Overview",
      "Research",
      "Companies",
      "Events",
      "Signals",
      "Backtests",
    ]);
  });

  it("marks only Overview as available", () => {
    expect(NAV_ITEMS.filter((item) => item.status === "available")).toHaveLength(1);
  });

  it("gives no href to any planned section", () => {
    for (const item of NAV_ITEMS.filter((entry) => entry.status === "planned")) {
      expect(item.href).toBeNull();
    }
  });

  it("has a unique id per entry", () => {
    const ids = NAV_ITEMS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
