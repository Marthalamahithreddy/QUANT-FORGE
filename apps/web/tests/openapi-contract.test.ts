/* eslint-disable @typescript-eslint/no-explicit-any -- tests mutate deep, deliberately untyped payloads to model drift */
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
import { decodeBenchmarkPage, decodeObservation } from "@/lib/api/decoders/research";
import { decodeRecordDetail, decodeRecordPage, decodeVerification } from "@/lib/api/decoders/records";
import { EVIDENCE_STATES } from "@/lib/api/record-types";
import { EVIDENCE_STATE_LABEL, EVIDENCE_STATE_SENTENCE, GAP_LABEL, GAP_SENTENCE, SECTION_LABEL, VERIFICATION_LABEL, VERIFICATION_SENTENCE } from "@/lib/record-wording";
import { REPRODUCIBILITY_LEVELS, REPRODUCIBILITY_META } from "@/lib/state";
import { RECORDS, clone as cloneRecord, recordDetail, recordVerify } from "./records-fixtures";
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
  ABNORMAL_RETURN_STATE,
  ACCOUNTING_STATE,
  ALIGNMENT_STATE,
  BENCHMARK_RETURN_STATE,
  CHANGE_STATE,
  COMPARATIVE_STATE,
  EPS_STATE,
  FILING_LINK_STATE,
  FORWARD_RETURN_STATE,
  LISTING_STATE,
  PERCENTAGE_STATE,
  PIT_STATE,
  POINT_IN_TIME_STATE,
  REFUSAL_CODE_STATE,
  SECTION_STATE,
  SURPRISE_STATE,
  TIMING_STATE,
} from "@/lib/state-map";
import {
  ACCOUNTING_STATE_LABEL,
  ALIGNMENT_LABEL,
  BENCHMARK_RETURN_LABEL,
  DISCLOSURE_WORDING,
  FILING_LINK_LABEL,
  FORWARD_RETURN_LABEL,
  LISTING_LABEL,
  PERIOD_VERIFICATION_NOTE,
  POINT_IN_TIME_STATE_LABEL,
  REFUSAL_SCOPE_ANCHOR,
  REFUSAL_SCOPE_LABEL,
  SECTION_STATE_LABEL,
} from "@/lib/research-wording";
import { captured, clone } from "./research-fixtures";
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
    ["SectionState", schema.SECTION_STATES],
    ["PointInTimeState", schema.POINT_IN_TIME_STATES],
    ["ResearchMode", schema.RESEARCH_MODES],
    ["PriceBasis", schema.PRICE_BASES],
    ["AlignmentStatus", schema.ALIGNMENT_STATUSES],
    ["ForwardReturnStatus", schema.FORWARD_RETURN_STATUSES],
    ["BenchmarkReturnStatus", schema.BENCHMARK_RETURN_STATUSES],
    ["AbnormalReturnStatus", schema.ABNORMAL_RETURN_STATUSES],
    ["FilingLinkStatus", schema.FILING_LINK_STATUSES],
    ["PeriodVerification", schema.PERIOD_VERIFICATIONS],
    ["AccountingState", schema.ACCOUNTING_STATES],
    ["DisclosureEvidenceLevel", schema.DISCLOSURE_LEVELS],
    ["RefusalScope", schema.REFUSAL_SCOPES],
    ["ListingResolutionStatus", schema.LISTING_RESOLUTION_STATUSES],
    ["Evidence", schema.LISTING_EVIDENCE],
  ];

  it.each(PAIRS)("%s", (name, list) => {
    expect(sorted(list)).toEqual(sorted(enumOf(name)));
  });

  it("matches the readiness and dependency vocabularies", () => {
    const readiness = (schemas.ReadinessResponse as { properties: { status: { enum: string[] } } }).properties.status.enum;
    expect(sorted(schema.READINESS_STATUSES)).toEqual(sorted(readiness));
    const stateOf = (name: string) => (schemas[name] as { properties: { state: { enum: string[] } } }).properties.state.enum;
    expect(sorted(schema.DATABASE_STATES)).toEqual(sorted(stateOf("DatabaseReadiness")));
    expect(sorted(schema.REDIS_STATES)).toEqual(sorted(stateOf("RedisReadiness")));
    expect(sorted(schema.SCHEMA_STATES)).toEqual(sorted(stateOf("SchemaReadiness")));
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
      ["AlignmentStatus", ALIGNMENT_STATE],
      ["ForwardReturnStatus", FORWARD_RETURN_STATE],
      ["BenchmarkReturnStatus", BENCHMARK_RETURN_STATE],
      ["AbnormalReturnStatus", ABNORMAL_RETURN_STATE],
      ["SectionState", SECTION_STATE],
      ["PointInTimeState", POINT_IN_TIME_STATE],
      ["FilingLinkStatus", FILING_LINK_STATE],
      ["AccountingState", ACCOUNTING_STATE],
      ["ListingResolutionStatus", LISTING_STATE],
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
      ["AlignmentStatus", ALIGNMENT_LABEL],
      ["ForwardReturnStatus", FORWARD_RETURN_LABEL],
      ["BenchmarkReturnStatus", BENCHMARK_RETURN_LABEL],
      ["SectionState", SECTION_STATE_LABEL],
      ["PointInTimeState", POINT_IN_TIME_STATE_LABEL],
      ["FilingLinkStatus", FILING_LINK_LABEL],
      ["AccountingState", ACCOUNTING_STATE_LABEL],
      ["ListingResolutionStatus", LISTING_LABEL],
      ["PeriodVerification", PERIOD_VERIFICATION_NOTE],
      ["DisclosureEvidenceLevel", DISCLOSURE_WORDING],
      ["RefusalScope", REFUSAL_SCOPE_LABEL],
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
    expect(Object.keys(snapshot.paths["/api/v1/earnings-events/{event_id}"]!.get.responses).sort()).toEqual(["200", "404", "422", "503"]);
    expect(Object.keys(snapshot.paths["/api/v1/earnings-events"]!.get.responses).sort()).toEqual(["200", "422", "503"]);
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
    [
      "ReadinessResponse",
      {
        status: "ready",
        service: "s",
        dependencies: { database: { state: "ok", gating: true }, redis: { state: "ok", gating: false } },
        schema: { state: "compatible", expected: "0014", current: "0014", gating: true },
      },
      (v) => decodeReadiness(v, "$"),
    ],
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

describe("research observation contract parity", () => {
  const params = (path: string) => snapshot.paths[path]!.get.parameters ?? [];
  const OBS = "/api/v1/research/observations/{event_id}";

  it("the observation request defines exactly the parameters the workspace may send (and the security knob it never sends)", () => {
    expect(params(OBS).map((p) => p.name).sort()).toEqual(["benchmark_id", "eps_kind", "eps_period_class", "event_id", "filing_accession", "horizons", "security_id"]);
    for (const p of params(OBS).filter((entry) => entry.in === "query")) expect(p.required ?? false, p.name).toBe(false);
  });

  it("the horizon rules and default are the API's own", () => {
    const horizons = params(OBS).find((p) => p.name === "horizons")!;
    const description = (horizons.schema as { description?: string }).description ?? "";
    expect(description).toMatch(new RegExp(`at most ${schema.HORIZON_MAX_COUNT}, each at most ${schema.HORIZON_MAX_VALUE}, no duplicates`));
    expect(description).toContain(`${schema.DEFAULT_HORIZONS.join(",")}`);
    const maxLength = ((horizons.schema as { anyOf: { maxLength?: number }[] }).anyOf.find((entry) => entry.maxLength) ?? {}).maxLength;
    expect(maxLength).toBe(64);
  });

  it("the filing parameters carry the accession pattern and the selector enums", () => {
    const filing = params(OBS).find((p) => p.name === "filing_accession")!.schema as { anyOf: { pattern?: string }[]; description: string };
    expect(filing.anyOf.find((entry) => entry.pattern)?.pattern).toBe("^[0-9]{10}-[0-9]{2}-[0-9]{6}$");
    expect(filing.description).toMatch(/Requires eps_period_class and eps_kind/);
    expect(JSON.stringify(params(OBS).find((p) => p.name === "eps_period_class")!.schema)).toContain("EpsPeriodClass");
    expect(JSON.stringify(params(OBS).find((p) => p.name === "eps_kind")!.schema)).toContain("EpsKind");
  });

  it("documents the status codes the unified result maps for the observation (404, 422, 503)", () => {
    expect(Object.keys(snapshot.paths[OBS]!.get.responses).sort()).toEqual(["200", "404", "422", "503"]);
  });

  it("the configured paths are the snapshot's paths", () => {
    expect(snapshot.paths[`${env.apiObservationsPath}/{event_id}`]).toBeDefined();
    expect(snapshot.paths[env.apiBenchmarksPath]).toBeDefined();
  });

  it("the benchmark index shares the page-size and offset bounds", () => {
    const { limit, offset } = Object.fromEntries(params(env.apiBenchmarksPath).map((p) => [p.name, p]));
    expect(limit?.schema).toMatchObject({ default: schema.PAGE_SIZE, maximum: schema.PAGE_LIMIT_MAX, minimum: 1 });
    expect(offset?.schema).toMatchObject({ default: 0, maximum: schema.PAGE_OFFSET_MAX, minimum: 0 });
    expect(Object.keys(snapshot.paths[env.apiBenchmarksPath]!.get.responses).sort()).toEqual(["200", "422", "503"]);
  });

  it("every refusal scope draws its codes only from enums the snapshot defines", () => {
    const enumsOf = (...names: string[]) => new Set(names.flatMap(enumOf));
    const ALLOWED: Record<string, Set<string>> = {
      EVENT: enumsOf("EpsSurpriseStatus"),
      MARKET: enumsOf("ListingResolutionStatus", "AlignmentStatus", "ForwardReturnStatus"),
      BENCHMARK: enumsOf("BenchmarkReturnStatus"),
      FILING_LINK: enumsOf("FilingLinkStatus"),
      ACCOUNTING: enumsOf("EpsStatus", "ComparativeEpsStatus", "PercentageUnavailableReason"),
      POINT_IN_TIME: enumsOf("PointInTimeEligibility", "AlignmentStatus", "ForwardReturnStatus"),
    };
    expect(Object.keys(REFUSAL_CODE_STATE).sort()).toEqual(sorted(enumOf("RefusalScope")));
    for (const [scope, vocabulary] of Object.entries(REFUSAL_CODE_STATE)) {
      for (const code of Object.keys(vocabulary)) expect(ALLOWED[scope]!.has(code), `${scope}/${code}`).toBe(true);
    }
    expect(Object.keys(REFUSAL_SCOPE_ANCHOR).sort()).toEqual(sorted(enumOf("RefusalScope")));
  });

  it("the same status never changes state between the tables it appears in (one code, one appearance)", () => {
    const seen = new Map<string, string>();
    for (const vocabulary of Object.values(REFUSAL_CODE_STATE)) {
      for (const [code, state] of Object.entries(vocabulary)) {
        if (seen.has(code)) expect(seen.get(code), code).toBe(state);
        seen.set(code, state);
      }
    }
  });
});

describe("observation decoders require every key the snapshot marks required", () => {
  const base = (name = "filing_evaluated") => clone(captured(name).response) as unknown as Record<string, any>;
  /** Replaces the object at `path` in a full observation, so one nested schema is exercised through the real decoder. */
  const through = (path: string) => (value: unknown) => {
    const root = base();
    const parts = path.split(".");
    const parent = parts.slice(0, -1).reduce((node: Record<string, any>, part) => node[part], root);
    parent[parts[parts.length - 1]!] = value;
    return decodeObservation(root, "$");
  };
  const eligible = () => clone(captured("pit_eligible").response) as unknown as Record<string, any>;

  const CASES: [string, unknown, (value: unknown) => unknown][] = [
    ["ResearchObservationResponse", base(), (v) => decodeObservation(v, "$")],
    ["IdentityOut", base().identity, through("identity")],
    ["ListingResolutionOut", base().identity.listing_resolution, through("identity.listing_resolution")],
    ["ObservationParametersOut", base().parameters, through("parameters")],
    ["EventOut", base().event, through("event")],
    ["FilingLinkOut", base().filing_link, through("filing_link")],
    ["DisclosureEvidenceOut", base().disclosure_evidence, through("disclosure_evidence")],
    ["AccountingOut", base().accounting, through("accounting")],
    ["DescriptiveOut", base().descriptive, through("descriptive")],
    ["PointInTimeOut", eligible().point_in_time, (v) => { const root = eligible(); root.point_in_time = v; return decodeObservation(root, "$"); }],
    ["StudyOut", base().descriptive.study, through("descriptive.study")],
    ["AlignmentOut", base().descriptive.study.alignment, through("descriptive.study.alignment")],
    ["SessionAlignmentOut", base().descriptive.study.alignment.session_alignment, through("descriptive.study.alignment.session_alignment")],
    ["StockReturnsOut", base().descriptive.study.stock_returns, through("descriptive.study.stock_returns")],
    ["BenchmarkOut", base().descriptive.study.benchmark, through("descriptive.study.benchmark")],
    ["EpsSurpriseOut", base().descriptive.study.eps_surprise, through("descriptive.study.eps_surprise")],
    ["ProvenanceOut", base().provenance, through("provenance")],
    ["ArtifactDigestOut", base().provenance.accounting_artifacts[0], (v) => { const root = base(); root.provenance.accounting_artifacts[0] = v; return decodeObservation(root, "$"); }],
    ["ForwardReturnOut", base().descriptive.study.stock_returns.forward_returns[0], (v) => { const root = base(); root.descriptive.study.stock_returns.forward_returns[0] = v; return decodeObservation(root, "$"); }],
    ["BenchmarkReturnOut", base().descriptive.study.benchmark.returns[0], (v) => { const root = base(); root.descriptive.study.benchmark.returns[0] = v; return decodeObservation(root, "$"); }],
    ["AbnormalReturnOut", base().descriptive.study.abnormal_returns[0], (v) => { const root = base(); root.descriptive.study.abnormal_returns[0] = v; return decodeObservation(root, "$"); }],
    ["RefusalOut", { scope: "MARKET", code: "UNKNOWN_TIMING", reason: "r", horizon: null }, (v) => { const root = base(); root.refusals = [v]; return decodeObservation(root, "$"); }],
    ["BenchmarkIndexPage", clone(captured("benchmark_selected")) && { items: [], total: 0, limit: 200, offset: 0 }, (v) => decodeBenchmarkPage(v, "$")],
    ["BenchmarkIndexItem", { id: "11111111-1111-4111-8111-111111111111", name: "n", symbol: null, currency: null, is_active: true }, (v) => decodeBenchmarkPage({ items: [v], total: 1, limit: 200, offset: 0 }, "$")],
  ];

  it.each(CASES)("%s: the fixture satisfies the schema's required list, and dropping any required key is rejected", (name, value, decode) => {
    const required = (schemas[name] as { required?: string[] }).required ?? [];
    expect(required.length).toBeGreaterThan(0);
    expect(() => decode(value)).not.toThrow();
    for (const key of required) {
      expect(Object.keys(value as Record<string, unknown>), `${name} fixture is missing ${key}`).toContain(key);
      const without = Object.fromEntries(Object.entries(value as Record<string, unknown>).filter(([k]) => k !== key));
      expect(() => decode(without), `${name}.${key} absent`).toThrow();
    }
  });
});


describe("saved research record contract parity", () => {
  const RECORDS_PATH = "/api/v1/research/records";
  const params = (path: string) => snapshot.paths[path]!.get.parameters ?? [];
  const statusEnum = (schemas.RecordVerificationResponse as { properties: { status: { enum: string[] } } }).properties.status.enum;
  const baselineEnum = (schemas.RecordVerificationResponse as { properties: { baseline: { enum: string[] } } }).properties.baseline.enum;
  const sectionEnum = (schemas.RecordVerificationResponse as { properties: { changed_sections: { items: { enum: string[] } } } }).properties.changed_sections.items.enum;

  it("the reproducibility levels and gaps equal the snapshot's, with no fallback member", () => {
    expect(sorted(REPRODUCIBILITY_LEVELS)).toEqual(sorted(enumOf("ReproducibilityLevel")));
    expect(sorted(REPRODUCIBILITY_LEVELS)).toEqual(["COMPLETE", "PARTIAL"]);
    expect(sorted(schema.REPRODUCIBILITY_GAPS)).toEqual(sorted(enumOf("ReproducibilityGap")));
    expect(Object.keys(REPRODUCIBILITY_META).sort()).toEqual(sorted(enumOf("ReproducibilityLevel")));
  });

  it("the verification statuses, baselines and sections equal the snapshot's, with no fallback member", () => {
    expect(sorted(schema.VERIFICATION_STATUSES)).toEqual(sorted(statusEnum));
    expect(sorted(schema.VERIFICATION_STATUSES)).toEqual(["EVIDENCE_INCOMPLETE", "MANIFEST_CHANGED", "MATCH", "RESULT_CHANGED"]);
    expect(sorted(schema.VERIFICATION_BASELINES)).toEqual(sorted(baselineEnum));
    expect(sorted(schema.MANIFEST_SECTIONS)).toEqual(sorted(sectionEnum));
  });

  it("every gap, status and section has wording, and nothing extra (exhaustive tables)", () => {
    expect(Object.keys(GAP_LABEL).sort()).toEqual(sorted(enumOf("ReproducibilityGap")));
    expect(Object.keys(GAP_SENTENCE).sort()).toEqual(sorted(enumOf("ReproducibilityGap")));
    expect(Object.keys(VERIFICATION_LABEL).sort()).toEqual(sorted(statusEnum));
    expect(Object.keys(VERIFICATION_SENTENCE).sort()).toEqual(sorted(statusEnum));
    expect(Object.keys(SECTION_LABEL).sort()).toEqual(sorted(sectionEnum));
    expect(Object.keys(EVIDENCE_STATE_LABEL).sort()).toEqual(sorted(EVIDENCE_STATES));
    expect(Object.keys(EVIDENCE_STATE_SENTENCE).sort()).toEqual(sorted(EVIDENCE_STATES));
  });

  it("the evidence-state list is not in the snapshot (the manifest is an open object), so it is pinned by the captured manifests", () => {
    expect((schemas.ResearchRecordDetail as { properties: { manifest: { additionalProperties: boolean } } }).properties.manifest.additionalProperties).toBe(true);
    expect((schemas.ResearchRecordDetail as { properties: { result: { additionalProperties: boolean } } }).properties.result.additionalProperties).toBe(true);
    const seen = new Set<string>();
    for (const entry of RECORDS.records.filter((r) => r.detail.status === 200)) {
      const evidence = entry.detail.body!.manifest.evidence;
      for (const part of ["stock", "benchmark", "filing", "accounting"]) seen.add(evidence[part].state);
    }
    expect([...seen].sort()).toEqual(sorted(EVIDENCE_STATES).filter((state) => state !== "NOT_READ"));
    expect(EVIDENCE_STATES).toContain("NOT_READ");
  });

  it("the list defines exactly limit and offset, with the shared bounds and a page size the API accepts", () => {
    expect(params(RECORDS_PATH).map((p) => p.name).sort()).toEqual(["limit", "offset"]);
    const { limit, offset } = Object.fromEntries(params(RECORDS_PATH).map((p) => [p.name, p]));
    expect(limit?.schema).toMatchObject({ default: schema.PAGE_SIZE, maximum: schema.PAGE_LIMIT_MAX, minimum: 1 });
    expect(offset?.schema).toMatchObject({ default: 0, maximum: schema.PAGE_OFFSET_MAX, minimum: 0 });
    expect(schema.RECORD_PAGE_SIZE).toBeGreaterThanOrEqual(1);
    expect(schema.RECORD_PAGE_SIZE).toBeLessThanOrEqual(schema.PAGE_LIMIT_MAX);
  });

  it("documents the status codes the unified result maps (404, 409, 422, 500, 503) and nothing that writes", () => {
    expect(Object.keys(snapshot.paths[RECORDS_PATH]!.get.responses).sort()).toEqual(["200", "422", "500", "503"]);
    expect(Object.keys(snapshot.paths[`${RECORDS_PATH}/{record_id}`]!.get.responses).sort()).toEqual(["200", "404", "422", "500", "503"]);
    expect(Object.keys(snapshot.paths[`${RECORDS_PATH}/{record_id}/verify`]!.get.responses).sort()).toEqual(["200", "404", "409", "422", "500", "503"]);
    for (const path of Object.keys(snapshot.paths).filter((p) => p.startsWith(RECORDS_PATH))) {
      expect(Object.keys(snapshot.paths[path]!), path).toEqual(["get"]);
    }
  });

  it("the configured path is the snapshot's", () => {
    expect(snapshot.paths[env.apiRecordsPath]).toBeDefined();
    expect(snapshot.paths[`${env.apiRecordsPath}/{record_id}/verify`]).toBeDefined();
  });

  it("the fingerprint pattern and algorithm are the API's", () => {
    const fp = (schemas.RecordFingerprints as { properties: Record<string, { pattern: string }> }).properties;
    for (const key of ["request", "methodology", "evidence", "manifest", "result"]) expect(fp[key]!.pattern).toBe("^[0-9a-f]{64}$");
    expect((schemas.ResearchRecordSummary as { properties: { hash_algorithm: { const: string } } }).properties.hash_algorithm.const).toBe(schema.HASH_ALGORITHM);
  });
});

describe("record decoders require every key the snapshot marks required", () => {
  const detail = () => cloneRecord(recordDetail("complete_filing_accounting")) as Record<string, any>;
  const verify = () => cloneRecord(recordVerify("manifest_changed")) as Record<string, any>;
  const list = () => cloneRecord(RECORDS.lists.limit_3_offset_0!.body) as Record<string, any>;
  const inVerify = (path: string) => (value: unknown) => {
    const root = verify();
    const parts = path.split(".");
    const parent = parts.slice(0, -1).reduce((node: Record<string, any>, part) => node[part], root);
    parent[parts[parts.length - 1]!] = value;
    return decodeVerification(root, "$");
  };

  const CASES: [string, unknown, (value: unknown) => unknown][] = [
    ["ResearchRecordPage", list(), (v) => decodeRecordPage(v, "$")],
    ["ResearchRecordSummary", list().items[0], (v) => decodeRecordPage({ ...list(), items: [v] }, "$")],
    ["RecordRequestSummary", list().items[0].request, (v) => { const root = list(); root.items[0].request = v; return decodeRecordPage(root, "$"); }],
    ["ResearchRecordDetail", detail(), (v) => decodeRecordDetail(v, "$")],
    ["RecordVerificationResponse", verify(), (v) => decodeVerification(v, "$")],
    ["RecordVerificationSide", verify().expected, inVerify("expected")],
    ["RecordFingerprints", verify().expected.fingerprints, inVerify("expected.fingerprints")],
  ];

  it.each(CASES)("%s: the fixture satisfies the schema's required list, and dropping any required key is rejected", (name, value, decode) => {
    const required = (schemas[name] as { required?: string[] }).required ?? [];
    expect(required.length).toBeGreaterThan(0);
    expect(() => decode(value)).not.toThrow();
    for (const key of required) {
      expect(Object.keys(value as Record<string, unknown>), `${name} fixture is missing ${key}`).toContain(key);
      const without = Object.fromEntries(Object.entries(value as Record<string, unknown>).filter(([k]) => k !== key));
      expect(() => decode(without), `${name}.${key} absent`).toThrow();
    }
  });
});
