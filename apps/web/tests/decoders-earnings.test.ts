import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { ContractError } from "@/lib/api/decode";
import { decodeEarningsEvent, decodeEarningsPage } from "@/lib/api/decoders/earnings";
import { decodeHealth, decodeReadiness } from "@/lib/api/decoders/health";
import { makeEvent } from "./fixtures";

type Json = Record<string, unknown>;
const event = (value: unknown) => decodeEarningsEvent(value, "$");
const page = (value: unknown) => decodeEarningsPage(value, "$");
const base = () => makeEvent() as unknown as Json;

/** `set(obj, "a.b", v)`: a deep copy with one value replaced (v === ABSENT removes the key). */
const ABSENT = Symbol("absent");
function withValue(root: Json, path: string, value: unknown): Json {
  const copy = JSON.parse(JSON.stringify(root)) as Json;
  const parts = path.split(".");
  let cursor: Json = copy;
  for (const part of parts.slice(0, -1)) cursor = cursor[part] as Json;
  const last = parts[parts.length - 1]!;
  if (value === ABSENT) delete cursor[last];
  else cursor[last] = value;
  return copy;
}

describe("pinned backend payloads", () => {
  const golden = JSON.parse(readFileSync(join(process.cwd(), "tests/contract/earnings.json"), "utf8")) as {
    page: Json & { items: Json[] };
    event: Json;
    health: Json;
    ready: Json;
  };

  it("the decoders accept exactly what the real backend returned", () => {
    expect(page(golden.page).items).toHaveLength(golden.page.items.length);
    expect(event(golden.event).id).toBe(golden.event.id);
    expect(decodeHealth(golden.health, "$").status).toBe("ok");
    expect(decodeReadiness(golden.ready, "$").dependencies).toBeDefined();
  });

  it("keep the documented values unchanged: strings stay strings, nulls stay null", () => {
    const decoded = event(golden.event);
    expect(decoded.reported_eps).toBe(golden.event.reported_eps);
    expect(decoded.estimated_eps).toBe(golden.event.estimated_eps);
    expect(decoded.estimated_revenue).toBe(golden.event.estimated_revenue);
    expect(decoded.event_timestamp).toBe(golden.event.event_timestamp);
    expect(decoded.resolved_tickers).toEqual(golden.event.resolved_tickers);
    expect(typeof decoded.estimated_eps === "string" || decoded.estimated_eps === null).toBe(true);
  });

  it("round-trips: decoding then serialising reproduces the payload's documented content", () => {
    expect(JSON.parse(JSON.stringify(event(golden.event)))).toEqual(golden.event);
  });
});

describe("null versus absent on the evidence fields", () => {
  const NULLABLE = [
    "reported_eps",
    "estimated_eps",
    "reported_revenue",
    "estimated_revenue",
    "currency",
    "event_timestamp",
    "fiscal_quarter",
    "company.cik",
    "availability.observed_at",
    "availability.observed_at_basis",
    "eps_surprise.absolute",
    "eps_surprise.ratio",
  ];

  it.each(NULLABLE)("%s: explicit null is valid, an absent key is contract drift", (path) => {
    expect(() => event(withValue(base(), path, null))).not.toThrow();
    expect(() => event(withValue(base(), path, ABSENT))).toThrow(ContractError);
    const outcome = (() => {
      try {
        event(withValue(base(), path, ABSENT));
      } catch (error) {
        return error as ContractError;
      }
    })();
    expect(outcome?.path).toBe(`$.${path}`);
  });

  it("listing.valid_to: null (still current) is valid, absent is drift", () => {
    const listing = (base().listings as Json[])[0]!;
    expect(() => event({ ...base(), listings: [{ ...listing, valid_to: null }] })).not.toThrow();
    const without = Object.fromEntries(Object.entries(listing).filter(([key]) => key !== "valid_to"));
    expect(() => event({ ...base(), listings: [without] })).toThrow(ContractError);
  });

  it("resolved_tickers must be present: an old payload without it is drift, an empty list is not", () => {
    expect(() => event(withValue(base(), "resolved_tickers", []))).not.toThrow();
    expect(() => event(withValue(base(), "resolved_tickers", ABSENT))).toThrow(ContractError);
    expect(() => event(withValue(base(), "resolved_tickers", null))).toThrow(ContractError);
  });

  it("a non-nullable field never accepts null", () => {
    for (const path of ["id", "event_date", "timing", "ingested_at", "eps_surprise.status", "availability.pit_reason", "company.display_name"]) {
      expect(() => event(withValue(base(), path, null)), path).toThrow(ContractError);
    }
  });
});

describe("decimal strings on every financial field", () => {
  const DECIMAL_FIELDS = ["reported_eps", "estimated_eps", "reported_revenue", "estimated_revenue", "eps_surprise.absolute", "eps_surprise.ratio"];

  it.each(DECIMAL_FIELDS)("%s: an exact string passes; a number, an exponent and padding are rejected", (path) => {
    expect(event(withValue(base(), path, "-1234.5600"))).toBeDefined();
    for (const bad of [1.5, 0, "1E-7", " 1.5", "1.5 ", "NaN", "1,5"]) {
      expect(() => event(withValue(base(), path, bad)), `${path} = ${String(bad)}`).toThrow(ContractError);
    }
  });

  it("keeps a 40-digit value exactly", () => {
    const long = `12345678901234567890.${"1234567890".repeat(2)}`;
    expect(event(withValue(base(), "reported_revenue", long)).reported_revenue).toBe(long);
  });

  it("does not repair a value into a number anywhere in the decoded event", () => {
    const decoded = event(withValue(base(), "reported_eps", "0.1000"));
    const numbers: string[] = [];
    (function walk(value: unknown, path: string) {
      if (typeof value === "number") numbers.push(path);
      else if (Array.isArray(value)) value.forEach((item, index) => walk(item, `${path}[${index}]`));
      else if (value && typeof value === "object") for (const [key, item] of Object.entries(value)) walk(item, `${path}.${key}`);
    })(decoded, "$");
    // Only counts: fiscal year and quarter. No financial field is a number.
    expect(numbers.sort()).toEqual(["$.fiscal_quarter", "$.fiscal_year"]);
  });
});

describe("unknown enums are rejected, never shown as a generic state", () => {
  const ENUM_FIELDS: [string, string][] = [
    ["timing", "OVERNIGHT"],
    ["source.provider_type", "PRESS_RELEASE"],
    ["availability.observed_at_basis", "COMPANY_PRESS_RELEASE"],
    ["availability.pit_eligibility", "MAYBE"],
    ["eps_surprise.status", "UNKNOWN_STATUS"],
  ];

  it.each(ENUM_FIELDS)("%s = %s", (path, bad) => {
    expect(() => event(withValue(base(), path, bad))).toThrow(ContractError);
    expect(() => event(withValue(base(), path, bad.toLowerCase()))).toThrow(ContractError);
  });

  it("an unknown security type inside a listing is rejected", () => {
    const listing = (base().listings as Json[])[0]!;
    expect(() => event({ ...base(), listings: [{ ...listing, security_type: "CRYPTO" }] })).toThrow(ContractError);
  });

  it("one bad row rejects the whole page (honesty over availability)", () => {
    const bad = withValue(base(), "timing", "OVERNIGHT");
    expect(() => page({ items: [base(), bad], total: 2, limit: 50, offset: 0 })).toThrow(ContractError);
  });
});

describe("shapes, identifiers and page envelope", () => {
  it.each([
    ["id", "not-a-uuid"],
    ["event_date", "2024-02-30"],
    ["event_timestamp", "2024-03-15"],
    ["ingested_at", "yesterday"],
    ["company.id", "x"],
    ["fiscal_year", "2024"],
    ["fiscal_quarter", 1.5],
  ])("%s = %j is rejected", (path, bad) => {
    expect(() => event(withValue(base(), path, bad))).toThrow(ContractError);
  });

  it("rejects a page whose envelope is malformed or inconsistent", () => {
    for (const bad of [
      { items: [], total: -1, limit: 50, offset: 0 },
      { items: [], total: 0, limit: 0, offset: 0 },
      { items: [], total: 0, limit: 201, offset: 0 },
      { items: [], total: 0, limit: 50, offset: 1_000_001 },
      { items: {}, total: 0, limit: 50, offset: 0 },
      { items: [], total: "0", limit: 50, offset: 0 },
      { items: [], total: 0, limit: 50 },
    ]) {
      expect(() => page(bad)).toThrow(ContractError);
    }
    expect(() => page({ items: [base(), base()], total: 2, limit: 1, offset: 0 })).toThrow(ContractError);
  });

  it("accepts an empty page as a valid answer", () => {
    expect(page({ items: [], total: 0, limit: 50, offset: 0 }).items).toEqual([]);
  });
});
