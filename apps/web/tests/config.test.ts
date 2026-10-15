import { describe, expect, it } from "vitest";

import { API_HEALTH_PATH, DEFAULT_API_BASE_URL, env } from "@/lib/config/env";
import { NAV_GROUPS, NAV_ITEMS, activeNavItem } from "@/lib/navigation";

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
  it("declares Overview, Evidence and Research groups in order", () => {
    expect(NAV_GROUPS.map((g) => g.label)).toEqual([null, "Evidence", "Research"]);
    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      "Overview",
      "Companies",
      "Earnings",
      "Filings",
      "Research",
      "Saved research",
    ]);
  });

  it("gives every entry a real route and shows no planned or disabled entries", () => {
    expect(NAV_ITEMS.map((item) => item.href)).toEqual([
      "/",
      "/companies",
      "/earnings",
      "/filings",
      "/research",
      "/research/saved",
    ]);
    expect(NAV_ITEMS.some((item) => "status" in item)).toBe(false);
  });

  it("includes Companies but not Backtests", () => {
    expect(NAV_ITEMS.map((item) => item.label)).toContain("Companies");
    expect(NAV_ITEMS.map((item) => item.label)).not.toContain("Backtests");
  });

  it("has a unique id per entry", () => {
    const ids = NAV_ITEMS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("picks the most specific entry as active", () => {
    expect(activeNavItem("/")?.id).toBe("overview");
    expect(activeNavItem("/research")?.id).toBe("research");
    expect(activeNavItem("/research/saved")?.id).toBe("saved");
    expect(activeNavItem("/research/saved/abc")?.id).toBe("saved");
    expect(activeNavItem("/earnings/123")?.id).toBe("earnings");
    expect(activeNavItem("/nowhere")).toBeNull();
  });
});
