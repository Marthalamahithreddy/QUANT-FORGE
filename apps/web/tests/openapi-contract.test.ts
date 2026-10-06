// @vitest-environment node
/**
 * The committed OpenAPI snapshot and what is built from it.
 *
 * Responsibilities, kept separate on purpose:
 * - the SNAPSHOT is the backend's own schema (refreshed by `npm run contract:export`; whether it
 *   still equals the live backend is `npm run contract:check`, which needs the backend toolchain);
 * - the GENERATED TYPES say what the backend claims, at compile time only;
 * - the RUNTIME enum lists, state maps and decoders say what this frontend accepts, and the tests
 *   below prove they equal the snapshot, so a backend enum change fails here instead of rendering
 *   as a generic "unknown".
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { decodeEarningsEvent, decodeEarningsPage } from "@/lib/api/decoders/earnings";
import { decodeFilingEps, decodeFilingIndexPage } from "@/lib/api/decoders/filings";
import { decodeHealth, decodeReadiness } from "@/lib/api/decoders/health";
import * as schema from "@/lib/api/schema";
import { BASIS_LABEL, PIT_PRESENTATION, TIMING_LABEL } from "@/lib/availability";
import {
  CHANGE_STATUS_LABEL,
  COMPARATIVE_STATUS_LABEL,
  EPS_KIND_LABEL,
  EPS_STATUS_LABEL,
  PERCENTAGE_REASON_DETAIL,
  PERIOD_CLASS_LABEL,
  TRANSITION_LABEL,
} from "@/lib/filings-view";
import {
  CHANGE_STATE,
  COMPARATIVE_STATE,
  EPS_STATE,
  PERCENTAGE_STATE,
  PIT_STATE,
  SURPRISE_STATE,
  TIMING_STATE,
} from "@/lib/state-map";
import { env } from "@/lib/config/env";
import { buildEarningsPath } from "@/lib/api/earnings";
import { buildFilingEpsPath, buildFilingsPath } from "@/lib/api/filings";
import { makeEvent } from "./fixtures";
import { makeEps, makeFiling } from "./filings-fixtures";
import { SNAPSHOT_PATH, TYPES_PATH, readText, typesFromSnapshot } from "../scripts/openapi-lib.mjs";

type Json = Record<string, unknown>;
interface Operation {
  parameters?: { name: string; in: string; required?: boolean; schema: Json }[];
  responses: Record<string, unknown>;
}
interface Snapshot {
  info: Json;
  paths: Record<string, { get: Operation }>;
  components: { schemas: Record<string, Json> };
}

const snapshotText = readFileSync(SNAPSHOT_PATH, "utf8");
const snapshot = JSON.parse(snapshotText) as Snapshot;
const schemas = snapshot.components.schemas;
const enumOf = (name: string): string[] => ((schemas[name] as { enum: string[] }).enum ?? []).slice();
const sorted = (values: readonly string[]) => [...values].sort();

describe("the committed snapshot", () => {
  it("is deterministic output: sorted keys, no timestamps, no machine paths, no hostnames", () => {
    expect(snapshotText.endsWith("\n")).toBe(true);
    expect(snapshotText).not.toMatch(/\/Users\/|\/home\/|[A-Z]:\\\\|localhost|127\.0\.0\.1|0\.0\.0\.0/);
    expect(snapshotText).not.toMatch(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
    // Re-serialising with sorted keys reproduces the bytes: nothing depends on insertion order.
    const sortedText = JSON.stringify(sortKeys(JSON.parse(snapshotText)), null, 2) + "\n";
    expect(sortedText).toBe(snapshotText);
  });

  it("describes the read-only API the frontend was designed against", () => {
    expect(Object.keys(snapshot.paths).sort()).toEqual([
      "/api/v1/benchmarks",
      "/api/v1/earnings-events",
      "/api/v1/earnings-events/{event_id}",
      "/api/v1/filings",
      "/api/v1/filings/{accession}/eps",
      "/api/v1/health",
      "/api/v1/health/ready",
      "/api/v1/research/observations/{event_id}",
      "/api/v1/research/records",
      "/api/v1/research/records/{record_id}",
      "/api/v1/research/records/{record_id}/verify",
      "/health",
    ]);
    for (const [path, methods] of Object.entries(snapshot.paths)) {
      expect(Object.keys(methods), path).toEqual(["get"]);
    }
  });
});

describe("generated types", () => {
  it("are exactly what openapi-typescript produces from the committed snapshot", async () => {
    expect(await typesFromSnapshot(snapshotText)).toBe(readText(TYPES_PATH));
  });

  it("are deterministic: the same snapshot yields the same bytes every time", async () => {
    expect(await typesFromSnapshot(snapshotText)).toBe(await typesFromSnapshot(snapshotText));
  });

  it("changing the snapshot changes the types (so staleness is detectable)", async () => {
    const changed = JSON.parse(snapshotText) as Snapshot;
    (changed.components.schemas.EpsKind as { enum: string[] }).enum.push("NEW_KIND");
    expect(await typesFromSnapshot(JSON.stringify(changed))).not.toBe(readText(TYPES_PATH));
  });

  it("stay type-only: the generated file has no runtime statements", () => {
    const code = readFileSync(TYPES_PATH, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    expect(code).not.toMatch(/^\s*(const|let|var|function|class)\s/m);
    expect(code).toMatch(/export interface paths/);
  });
});

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortKeys((value as Json)[key])]));
  }
  return value;
}

describe("enum parity: runtime lists equal the snapshot", () => {
  const PAIRS: [string, readonly string[]][] = [
    ["SecFormType", schema.FILING_FORMS],
    ["EpsPeriodClass", schema.PERIOD_CLASSES],
    ["EpsKind", schema.EPS_KINDS],
    ["EpsStatus", schema.EPS_STATUSES],
    ["ComparativeEpsStatus", schema.COMPARATIVE_STATUSES],
    ["EpsChangeStatus", schema.CHANGE_STATUSES],
    ["EpsTransition", schema.TRANSITIONS],
    ["PercentageUnavailableReason", schema.PERCENTAGE_REASONS],
    ["EarningsTiming", schema.EARNINGS_TIMINGS],
    ["AvailabilityBasis", schema.AVAILABILITY_BASES],
    ["PointInTimeEligibility", schema.PIT_ELIGIBILITIES],
    ["EpsSurpriseStatus", schema.EPS_SURPRISE_STATUSES],
    ["ProviderType", schema.PROVIDER_TYPES],
    ["SecurityType", schema.SECURITY_TYPES],
  ];

  it.each(PAIRS)("%s", (name, list) => {
    expect(sorted(list)).toEqual(sorted(enumOf(name)));
  });

  it("matches the readiness and dependency vocabularies", () => {
    const readiness = (schemas.ReadinessResponse as { properties: { status: { enum: string[] } } }).properties.status.enum;
    expect(sorted(schema.READINESS_STATUSES)).toEqual(sorted(readiness));
    const dependency = (schemas.DependencyReadiness as { properties: { database: { enum: string[] } } }).properties.database.enum;
    expect(sorted(schema.DEPENDENCY_STATES)).toEqual(sorted(dependency));
  });

  it("covers every status enum the migrated pages consume with a state mapping (exhaustive)", () => {
    const MAPPED: [string, Record<string, unknown>][] = [
      ["EpsSurpriseStatus", SURPRISE_STATE],
      ["PointInTimeEligibility", PIT_STATE],
      ["EpsStatus", EPS_STATE],
      ["ComparativeEpsStatus", COMPARATIVE_STATE],
      ["EpsChangeStatus", CHANGE_STATE],
      ["PercentageUnavailableReason", PERCENTAGE_STATE],
      ["EarningsTiming", TIMING_STATE],
    ];
    for (const [name, map] of MAPPED) expect(sorted(Object.keys(map)), name).toEqual(sorted(enumOf(name)));
  });

  it("covers every enum the pages display with a wording table", () => {
    const WORDING: [string, Record<string, unknown>][] = [
      ["EpsStatus", EPS_STATUS_LABEL],
      ["ComparativeEpsStatus", COMPARATIVE_STATUS_LABEL],
      ["EpsChangeStatus", CHANGE_STATUS_LABEL],
      ["EpsTransition", TRANSITION_LABEL],
      ["PercentageUnavailableReason", PERCENTAGE_REASON_DETAIL],
      ["EpsPeriodClass", PERIOD_CLASS_LABEL],
      ["EpsKind", EPS_KIND_LABEL],
      ["EarningsTiming", TIMING_LABEL],
      ["AvailabilityBasis", BASIS_LABEL],
      ["PointInTimeEligibility", PIT_PRESENTATION],
    ];
    for (const [name, map] of WORDING) expect(sorted(Object.keys(map)), name).toEqual(sorted(enumOf(name)));
  });
});

describe("request parity: parameters, bounds and requirements", () => {
  const params = (path: string) => snapshot.paths[path]!.get.parameters ?? [];
  const named = (path: string) => Object.fromEntries(params(path).map((p) => [p.name, p]));

  it("the earnings list sends exactly the parameters the endpoint defines", () => {
    const names = params("/api/v1/earnings-events").map((p) => p.name).sort();
    expect(names).toEqual(["date_from", "date_to", "limit", "offset", "ticker"]);
    const sent = [...new URL(buildEarningsPath({ ticker: "A", dateFrom: "2024-01-01", dateTo: "2024-02-01" }), "http://x").searchParams.keys()].sort();
    expect(sent).toEqual(names);
  });

  it("the filings list sends exactly the parameters the endpoint defines", () => {
    const names = params("/api/v1/filings").map((p) => p.name).sort();
    expect(names).toEqual(["cik", "form", "limit", "offset"]);
    const sent = [...new URL(buildFilingsPath({ form: "10-Q", cik: "0000320193" }), "http://x").searchParams.keys()].sort();
    expect(sent).toEqual(names);
  });

  it("the filing EPS request requires both selectors and treats the CIK as optional", () => {
    const p = named("/api/v1/filings/{accession}/eps");
    expect(p.period_class?.required).toBe(true);
    expect(p.eps_kind?.required).toBe(true);
    expect(p.cik?.required ?? false).toBe(false);
    expect(p.accession?.in).toBe("path");
    const without = new URL(buildFilingEpsPath("0000320193-24-000010", "QUARTER", "DILUTED"), "http://x").searchParams;
    expect([...without.keys()].sort()).toEqual(["eps_kind", "period_class"]);
  });

  it("page size and offset bounds equal the API's", () => {
    for (const path of ["/api/v1/earnings-events", "/api/v1/filings"]) {
      const { limit, offset } = named(path);
      expect(limit?.schema).toMatchObject({ default: schema.PAGE_SIZE, maximum: schema.PAGE_LIMIT_MAX, minimum: 1 });
      expect(offset?.schema).toMatchObject({ default: 0, maximum: schema.PAGE_OFFSET_MAX, minimum: 0 });
    }
  });

  it("patterns for CIK and accession equal the API's", () => {
    const cik = named("/api/v1/filings").cik!.schema as { anyOf: { pattern?: string }[] };
    expect(cik.anyOf.find((entry) => entry.pattern)?.pattern).toBe("^[0-9]{10}$");
    expect(schema.CIK_PATTERN.source).toBe("^\\d{10}$");
    const accession = named("/api/v1/filings/{accession}/eps").accession!.schema as { pattern: string };
    expect(accession.pattern).toBe("^[0-9]{10}-[0-9]{2}-[0-9]{6}$");
    expect(schema.ACCESSION_PATTERN.source).toBe("^\\d{10}-\\d{2}-\\d{6}$");
  });

  it("the configured API paths are the snapshot's paths", () => {
    for (const path of [env.apiHealthPath, env.apiReadinessPath, env.apiEarningsPath, env.apiFilingsPath]) {
      expect(snapshot.paths[path], path).toBeDefined();
    }
  });

  it("documents the status codes the unified result maps (404, 422, 503)", () => {
    expect(Object.keys(snapshot.paths["/api/v1/earnings-events/{event_id}"]!.get.responses).sort()).toEqual(["200", "404", "422"]);
    expect(Object.keys(snapshot.paths["/api/v1/filings"]!.get.responses).sort()).toEqual(["200", "422", "503"]);
    expect(Object.keys(snapshot.paths["/api/v1/health/ready"]!.get.responses).sort()).toEqual(["200", "503"]);
  });
});

describe("decoders require every key the snapshot marks required", () => {
  const fact = (makeEps().current as { fact: Json }).fact;
  const eps = makeEps() as Json;
  const event = makeEvent() as unknown as Json;
  const CASES: [string, unknown, (value: unknown) => unknown][] = [
    ["EarningsEventItem", event, (v) => decodeEarningsEvent(v, "$")],
    ["CompanyRef", event.company, (v) => decodeEarningsEvent({ ...event, company: v }, "$")],
    ["SourceRef", event.source, (v) => decodeEarningsEvent({ ...event, source: v }, "$")],
    ["ListingRef", (event.listings as unknown[])[0], (v) => decodeEarningsEvent({ ...event, listings: [v] }, "$")],
    ["Availability", event.availability, (v) => decodeEarningsEvent({ ...event, availability: v }, "$")],
    ["EpsSurprise", event.eps_surprise, (v) => decodeEarningsEvent({ ...event, eps_surprise: v }, "$")],
    ["EarningsEventPage", { items: [event], total: 1, limit: 50, offset: 0 }, (v) => decodeEarningsPage(v, "$")],
    ["HealthResponse", { status: "ok", service: "s", version: "1", environment: "e" }, (v) => decodeHealth(v, "$")],
    ["ReadinessResponse", { status: "ready", service: "s", dependencies: { database: "ok", redis: "ok" } }, (v) => decodeReadiness(v, "$")],
    ["FilingIndexItem", makeFiling(), (v) => decodeFilingIndexPage({ items: [v], total: 1, limit: 50, offset: 0 }, "$")],
    ["FilingIndexPage", { items: [makeFiling()], total: 1, limit: 50, offset: 0 }, (v) => decodeFilingIndexPage(v, "$")],
    ["FilingEpsResponse", eps, (v) => decodeFilingEps(v, "$")],
    ["CurrentEpsSection", eps.current, (v) => decodeFilingEps({ ...eps, current: v }, "$")],
    ["ComparativeEpsSection", eps.comparative, (v) => decodeFilingEps({ ...eps, comparative: v }, "$")],
    ["EpsChangeSection", eps.change, (v) => decodeFilingEps({ ...eps, change: v }, "$")],
    ["FactEvidence", fact, (v) => decodeFilingEps({ ...eps, current: { ...(eps.current as Json), fact: v } }, "$")],
  ];

  it.each(CASES)("%s: the fixture satisfies the schema's required list, and dropping any required key is rejected", (name, value, decode) => {
    const required = (schemas[name] as { required?: string[] }).required ?? [];
    expect(required.length).toBeGreaterThan(0);
    expect(() => decode(value)).not.toThrow();
    for (const key of required) {
      expect(Object.keys(value as Json), `${name} fixture is missing ${key}`).toContain(key);
      const without = Object.fromEntries(Object.entries(value as Json).filter(([name]) => name !== key));
      expect(() => decode(without), `${name}.${key} absent`).toThrow();
    }
  });
});
