/* eslint-disable @typescript-eslint/no-explicit-any -- tests mutate deep, deliberately untyped payloads to model drift */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OBSERVATION_TIMEOUT_MS, READ_TIMEOUT_MS } from "@/lib/api/client";
import { ContractError } from "@/lib/api/decode";
import { decodeBenchmarkPage, decodeObservation } from "@/lib/api/decoders/research";
import { buildObservationPath, effectiveHorizons, getBenchmarks, getObservation } from "@/lib/api/research";
import { failureCopy } from "@/lib/api/messages";
import { FORWARD_RETURN_LABEL } from "@/lib/research-wording";
import { FORWARD_RETURN_STATE } from "@/lib/state-map";
import { CAPTURE, EMPTY_BENCHMARK_ID, BENCHMARK_ID, captured, clone } from "./research-fixtures";
import { jsonResponse } from "./fixtures";

type Json = Record<string, any>;
const decodeOne = (value: unknown) => decodeObservation(value, "$");
const base = () => clone(captured("benchmark_selected")) as unknown as { response: Json; event_id: string; params: Record<string, string> };

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("pinned backend observations", () => {
  it.each(CAPTURE.cases.map((entry) => [entry.name, entry] as const))("the decoder accepts the real response: %s", (_name, entry) => {
    expect(() => decodeOne(entry.response)).not.toThrow();
  });

  it("covers the states the workspace must show", () => {
    const seen = (pick: (r: Json) => unknown) => new Set(CAPTURE.cases.map((c) => pick(c.response as unknown as Json)));
    expect(seen((r) => r.point_in_time.state)).toEqual(new Set(["REFUSED", "EVIDENCE_ELIGIBLE"]));
    expect(seen((r) => r.filing_link.status)).toEqual(
      new Set(["NOT_LINKED", "CALLER_SUPPLIED_CIK_CONSISTENT", "FILING_NOT_STORED", "CIK_MISMATCH_REFUSED"]),
    );
    expect(seen((r) => r.accounting.state)).toEqual(new Set(["NOT_REQUESTED", "EVALUATED", "LINK_REFUSED"]));
    expect(seen((r) => r.disclosure_evidence.level)).toEqual(new Set(["NONE", "D0_FILING_EXISTS", "D1_ITEM_2_02_DECLARED"]));
    expect(seen((r) => r.descriptive.state)).toEqual(new Set(["AVAILABLE", "PARTIAL", "UNAVAILABLE"]));
    // Sessions come from the exchange calendar: an event with no stored bar is still ALIGNED.
    expect(seen((r) => r.descriptive.study.alignment.session_alignment?.status)).toEqual(new Set(["ALIGNED", "UNKNOWN_TIMING"]));
  });

  it("covers each of the three unavailable forward-return statuses, with the 12B/11.2E semantics", () => {
    const forward = CAPTURE.cases.flatMap((c) => (c.response as unknown as Json).descriptive.study.stock_returns.forward_returns as Json[]);
    const byStatus = (status: string) => forward.filter((f) => f.status === status);
    // the calendar proves the target session and no price is stored: the proven session is reported, never moved
    for (const f of byStatus("MISSING_TARGET_PRICE")) {
      expect(f.target_session).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(f.target_close).toBeNull();
      expect(f.reason).toMatch(/exchange session after the anchor .*no price observation is stored for it; the target is not moved/);
    }
    // the calendar cannot prove the session: no session is named at all
    for (const f of byStatus("INSUFFICIENT_FORWARD_SESSIONS")) {
      expect(f.target_session).toBeNull();
      expect(f.reason).toMatch(/^the exchange calendar proves fewer than \d+ trading sessions after the anchor session/);
    }
    // the proven anchor session has no stored price
    for (const f of byStatus("MISSING_ANCHOR_PRICE")) expect(f.anchor_close).toBeNull();
    expect(byStatus("MISSING_TARGET_PRICE").length).toBeGreaterThan(0);
    expect(byStatus("INSUFFICIENT_FORWARD_SESSIONS").length).toBeGreaterThan(0);
    expect(byStatus("MISSING_ANCHOR_PRICE").length).toBeGreaterThan(0);
    // none of the pre-11.2E / pre-12B wording survives in the pinned captures
    expect(JSON.stringify(CAPTURE)).not.toMatch(/available trading sessions exist after|ordered available trading-session|ordered list of available trading sessions/);
  });

  it("keeps every financial value an exact string and never produces a JS number for one", () => {
    const FINANCIAL = new Set([
      "return_value", "benchmark_return", "abnormal_return", "anchor_close", "target_close", "reported_eps", "estimated_eps",
      "absolute_surprise", "surprise_ratio", "value", "eps_change", "percentage_change",
    ]);
    const offenders: string[] = [];
    (function walk(node: unknown, path: string) {
      if (Array.isArray(node)) node.forEach((item, index) => walk(item, `${path}[${index}]`));
      else if (node && typeof node === "object") {
        for (const [key, value] of Object.entries(node)) {
          if (FINANCIAL.has(key) && value !== null && typeof value !== "string") offenders.push(`${path}.${key}`);
          walk(value, `${path}.${key}`);
        }
      }
    })(decodeOne(captured("filing_evaluated").response), "$");
    expect(offenders).toEqual([]);
  });

  it("returns exactly the documented keys (a fresh object, extras not copied)", () => {
    const decoded = decodeOne({ ...base().response, extra: 1 }) as unknown as Json;
    expect(Object.keys(decoded)).toEqual(["identity", "parameters", "event", "filing_link", "disclosure_evidence", "accounting", "descriptive", "point_in_time", "provenance", "refusals"]);
  });
});

describe("null versus absent in the evidence", () => {
  const set = (path: string, value: unknown, root: Json = base().response) => {
    const copy = clone(root);
    const parts = path.split(".");
    let cursor = copy;
    for (const part of parts.slice(0, -1)) cursor = Array.isArray(cursor) ? cursor[Number(part)] : cursor[part];
    const last = parts[parts.length - 1]!;
    if (value === undefined) delete cursor[last];
    else cursor[last] = value;
    return copy;
  };

  const NULLABLE = [
    "identity.company_cik", "identity.benchmark_id", "identity.filing_accession", "identity.listing_resolution.evidence",
    "event.event_timestamp", "event.observed_at", "event.observed_at_basis", "event.fiscal_quarter",
    "filing_link.accession_number", "filing_link.company_cik", "filing_link.form", "disclosure_evidence.items",
    "provenance.event_ingestion_run_id", "provenance.filing_source_id", "provenance.filing_ingestion_run_id",
    "descriptive.study.stock_returns.anchor_session", "descriptive.study.eps_surprise.surprise_ratio",
    "parameters.eps_kind", "parameters.eps_period_class", "parameters.filing_accession", "parameters.security_id",
  ];

  it.each(NULLABLE)("%s: explicit null is valid, an absent key is contract drift", (path) => {
    expect(() => decodeOne(set(path, null))).not.toThrow();
    expect(() => decodeOne(set(path, undefined))).toThrow(ContractError);
  });

  it("a point-in-time REFUSED study is null (valid); absent is drift", () => {
    expect(base().response.point_in_time.study).toBeNull();
    expect(() => decodeOne(set("point_in_time.study", undefined))).toThrow(ContractError);
  });

  it("accounting.eps is null when not evaluated (valid); absent is drift", () => {
    expect(base().response.accounting.eps).toBeNull();
    expect(() => decodeOne(set("accounting.eps", undefined))).toThrow(ContractError);
  });

  it("a return's price and session fields are null (valid) for a refused horizon, never absent", () => {
    const refused = clone(captured("unknown_timing").response) as unknown as Json;
    expect(refused.descriptive.study.stock_returns.forward_returns[0].return_value).toBeNull();
    expect(() => decodeOne(refused)).not.toThrow();
    delete refused.descriptive.study.stock_returns.forward_returns[0].target_session;
    expect(() => decodeOne(refused)).toThrow(ContractError);
  });
});

describe("exact decimals and enums", () => {
  const withReturn = (value: unknown) => {
    const copy = base().response;
    copy.descriptive.study.stock_returns.forward_returns[0].return_value = value;
    return copy;
  };

  it.each([[0.1], [1], ["1E-7"], [" 0.1"], ["10%"], ["NaN"], ["0,1"]])("rejects %j as a return", (bad) => {
    expect(() => decodeOne(withReturn(bad))).toThrow(ContractError);
  });

  it("keeps a 50-digit return exactly", () => {
    const exact = `0.${"1234567890".repeat(5)}`;
    const decoded = decodeOne(withReturn(exact)) as unknown as Json;
    expect(decoded.descriptive.study.stock_returns.forward_returns[0].return_value).toBe(exact);
  });

  it.each([
    "descriptive.state", "point_in_time.state", "point_in_time.eligibility", "filing_link.status", "filing_link.period_verification",
    "accounting.state", "disclosure_evidence.level", "parameters.price_basis", "identity.listing_resolution.status", "event.timing",
  ])("rejects an unknown member for %s instead of showing a generic state", (path) => {
    const copy = base().response;
    const parts = path.split(".");
    let cursor = copy;
    for (const part of parts.slice(0, -1)) cursor = cursor[part];
    cursor[parts[parts.length - 1]!] = "SOMETHING_NEW";
    expect(() => decodeOne(copy)).toThrow(ContractError);
  });

  it.each([
    ["alignment", (r: Json) => (r.descriptive.study.alignment.session_alignment.status = "NEW_STATUS")],
    ["forward return", (r: Json) => (r.descriptive.study.stock_returns.forward_returns[0].status = "NEW_STATUS")],
    ["benchmark return", (r: Json) => (r.descriptive.study.benchmark.returns[0].status = "NEW_STATUS")],
    ["abnormal return", (r: Json) => (r.descriptive.study.abnormal_returns[0].status = "NEW_STATUS")],
    ["EPS surprise", (r: Json) => (r.descriptive.study.eps_surprise.status = "NEW_STATUS")],
    ["research mode", (r: Json) => (r.descriptive.study.research_mode = "LIVE")],
  ])("rejects an unknown %s status", (_name, mutate) => {
    const copy = base().response;
    mutate(copy);
    expect(() => decodeOne(copy)).toThrow(ContractError);
  });
});

describe("Step 12B: a proven target without a stored price", () => {
  const withTargetStatus = (status: string) => {
    const copy = base().response;
    Object.assign(copy.descriptive.study.stock_returns.forward_returns[1], {
      status,
      target_session: "2026-03-09",
      target_close: null,
      return_value: null,
    });
    return copy;
  };

  it("decodes MISSING_TARGET_PRICE and keeps the proven target session with no close", () => {
    const decoded = decodeOne(withTargetStatus("MISSING_TARGET_PRICE")) as unknown as Json;
    const forward = decoded.descriptive.study.stock_returns.forward_returns[1];
    expect(forward.status).toBe("MISSING_TARGET_PRICE");
    expect(forward.target_session).toBe("2026-03-09");
    expect(forward.target_close).toBeNull();
    expect(forward.return_value).toBeNull();
  });

  it("still rejects a status the generated contract does not know (no permissive fallback)", () => {
    expect(() => decodeOne(withTargetStatus("TARGET_NOT_YET_OBSERVABLE"))).toThrow(ContractError);
  });

  it("maps each status to its own plain wording and state", () => {
    expect(FORWARD_RETURN_LABEL.MISSING_TARGET_PRICE).toBe("Price not stored for target session");
    expect(FORWARD_RETURN_LABEL.INSUFFICIENT_FORWARD_SESSIONS).toBe("Beyond the calendar's proven horizon");
    expect(FORWARD_RETURN_LABEL.INSUFFICIENT_FORWARD_SESSIONS).not.toMatch(/stor/i); // no price-storage claim
    expect(FORWARD_RETURN_STATE.MISSING_TARGET_PRICE).toBe("MISSING");
    for (const label of Object.values(FORWARD_RETURN_LABEL)) expect(label).not.toMatch(/future|pending|not yet/i);
  });
});

describe("refusals: codes belong to their scope's vocabulary", () => {
  const withRefusal = (scope: string, code: string) => {
    const copy = base().response;
    copy.refusals = [{ scope, code, reason: "r", horizon: null }];
    return copy;
  };

  it("accepts codes the backend's layers really emit, per scope", () => {
    for (const [scope, code] of [
      ["EVENT", "MISSING_REPORTED_EPS"], ["MARKET", "INSUFFICIENT_FORWARD_SESSIONS"], ["MARKET", "UNKNOWN_TIMING"], ["MARKET", "AMBIGUOUS_LISTING"],
      ["BENCHMARK", "MISSING_ANCHOR_PRICE"], ["FILING_LINK", "CIK_MISMATCH_REFUSED"], ["ACCOUNTING", "COMPARATIVE_MISSING"],
      ["ACCOUNTING", "UNSUPPORTED_FORM_PERIOD"], ["ACCOUNTING", "ZERO_COMPARATIVE"], ["POINT_IN_TIME", "INELIGIBLE_NO_AVAILABILITY_EVIDENCE"],
      ["POINT_IN_TIME", "MISSING_ANCHOR_PRICE"], ["MARKET", "MISSING_TARGET_PRICE"], ["POINT_IN_TIME", "MISSING_TARGET_PRICE"],
      ["BENCHMARK", "MISSING_TARGET_PRICE"],
    ] as const) {
      expect(() => decodeOne(withRefusal(scope, code)), `${scope}/${code}`).not.toThrow();
    }
  });

  it.each([
    ["MARKET", "SOMETHING_NEW"],
    ["BENCHMARK", "CIK_MISMATCH_REFUSED"],
    ["FILING_LINK", "MISSING_ANCHOR_PRICE"],
    ["EVENT", "ELIGIBLE"],
    ["MARKET", "ALIGNED"],
    ["MARKET", "AVAILABLE"],
    ["BENCHMARK", "AVAILABLE"],
  ])("rejects %s/%s as drift: no state is invented for an unknown or misplaced code", (scope, code) => {
    expect(() => decodeOne(withRefusal(scope, code))).toThrow(ContractError);
  });

  it("rejects an unknown scope", () => {
    expect(() => decodeOne(withRefusal("NEW_SCOPE", "UNKNOWN_TIMING"))).toThrow(ContractError);
  });
});

describe("coherence the decoder enforces", () => {
  it("a point-in-time study exists exactly when the evidence is eligible", () => {
    const eligible = clone(captured("pit_eligible").response) as unknown as Json;
    expect(() => decodeOne(eligible)).not.toThrow();
    const noStudy = clone(eligible);
    noStudy.point_in_time.study = null;
    expect(() => decodeOne(noStudy)).toThrow(ContractError);
    const refusedWithStudy = base().response;
    refusedWithStudy.point_in_time.study = eligible.point_in_time.study;
    expect(() => decodeOne(refusedWithStudy)).toThrow(ContractError);
  });

  it("the filing EPS is present exactly when the accounting was evaluated", () => {
    const evaluated = clone(captured("filing_evaluated").response) as unknown as Json;
    const missing = clone(evaluated);
    missing.accounting.eps = null;
    expect(() => decodeOne(missing)).toThrow(ContractError);
    const stray = base().response;
    stray.accounting.eps = evaluated.accounting.eps;
    expect(() => decodeOne(stray)).toThrow(ContractError);
  });

  it("the evaluated EPS passes through the strict filing decoder (a bad fact is drift)", () => {
    const evaluated = clone(captured("filing_evaluated").response) as unknown as Json;
    evaluated.accounting.eps.current.fact.value = 1.53;
    expect(() => decodeOne(evaluated)).toThrow(ContractError);
  });

  it("the three per-horizon lists share horizons and order, so a figure is never paired with another horizon", () => {
    const copy = base().response;
    copy.descriptive.study.benchmark.returns.reverse();
    expect(() => decodeOne(copy)).toThrow(ContractError);
    const shorter = base().response;
    shorter.descriptive.study.abnormal_returns.pop();
    expect(() => decodeOne(shorter)).toThrow(ContractError);
  });

  it("a figure exists exactly when its status is AVAILABLE", () => {
    const hollow = base().response;
    hollow.descriptive.study.stock_returns.forward_returns[0].return_value = null;
    expect(() => decodeOne(hollow)).toThrow(ContractError);
    const phantom = clone(captured("unknown_timing").response) as unknown as Json;
    phantom.descriptive.study.stock_returns.forward_returns[0].return_value = "0.1";
    expect(() => decodeOne(phantom)).toThrow(ContractError);
  });

  it("parameters.horizons are ascending, unique, at most ten and within 1..252", () => {
    for (const bad of [[2, 1], [1, 1], [0], [253], Array.from({ length: 11 }, (_, i) => i + 1), []]) {
      const copy = base().response;
      copy.parameters.horizons = bad;
      expect(() => decodeOne(copy), JSON.stringify(bad)).toThrow(ContractError);
    }
  });

  it("an artifact digest is a SHA-256 hex string", () => {
    const evaluated = clone(captured("filing_evaluated").response) as unknown as Json;
    evaluated.provenance.accounting_artifacts[0].sha256 = "not-a-digest";
    expect(() => decodeOne(evaluated)).toThrow(ContractError);
  });

  it("carries no field the live observation must not claim (no reproducibility level, no disclosure time)", () => {
    const keys = new Set<string>();
    (function walk(node: unknown) {
      if (Array.isArray(node)) node.forEach(walk);
      else if (node && typeof node === "object") for (const [k, v] of Object.entries(node)) {
        keys.add(k);
        walk(v);
      }
    })(CAPTURE.cases.map((c) => c.response));
    for (const forbidden of ["reproducibility_level", "reproducibility_gaps", "disclosed_at", "accepted_at", "known_at", "signal", "score", "confidence", "rank"]) {
      expect(keys.has(forbidden), forbidden).toBe(false);
    }
  });
});

describe("benchmark index decoder", () => {
  it("accepts the real page (active and inactive alike) and rejects drift", () => {
    expect(decodeBenchmarkPage(CAPTURE.benchmarks, "$").items.map((b) => b.is_active).sort()).toEqual([false, true, true]);
    const missing = clone(CAPTURE.benchmarks) as Json;
    delete missing.items[0].is_active;
    expect(() => decodeBenchmarkPage(missing, "$")).toThrow(ContractError);
    const nullable = clone(CAPTURE.benchmarks) as Json;
    nullable.items[0].symbol = null;
    nullable.items[0].currency = null;
    expect(() => decodeBenchmarkPage(nullable, "$")).not.toThrow();
    delete nullable.items[0].symbol;
    expect(() => decodeBenchmarkPage(nullable, "$")).toThrow(ContractError);
  });
});

describe("client: request construction and the 10 s class", () => {
  const event = captured("benchmark_selected");

  it("sends only what was asked: no benchmark, no filing, no security by default", () => {
    expect(buildObservationPath(event.event_id)).toBe(`/api/v1/research/observations/${event.event_id}`);
    expect(buildObservationPath(event.event_id, { horizons: [1, 5] })).toContain("horizons=1%2C5");
    expect(buildObservationPath(event.event_id, {})).not.toMatch(/benchmark|filing|security|eps_/);
  });

  it("sends the selectors only together with a filing", () => {
    const path = buildObservationPath(event.event_id, { epsPeriodClass: "QUARTER", epsKind: "DILUTED" });
    expect(path).not.toMatch(/eps_/);
    expect(buildObservationPath(event.event_id, { filingAccession: "0000320193-24-000010", epsPeriodClass: "QUARTER", epsKind: "DILUTED" })).toMatch(
      /filing_accession=0000320193-24-000010&eps_period_class=QUARTER&eps_kind=DILUTED/,
    );
  });

  it("uses the 10 s class for the observation and keeps 4 s for the benchmark index", async () => {
    expect(OBSERVATION_TIMEOUT_MS).toBe(10_000);
    expect(READ_TIMEOUT_MS).toBe(4_000);
    const spy = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockResolvedValue(jsonResponse(event.response));
    await getObservation(event.event_id, { horizons: [1, 2], benchmarkId: BENCHMARK_ID });
    expect(spy).toHaveBeenLastCalledWith(10_000);
    fetchMock.mockResolvedValue(jsonResponse({ ...CAPTURE.benchmarks, limit: 200, offset: 0 }));
    await getBenchmarks();
    expect(spy).toHaveBeenLastCalledWith(4_000);
  });

  it("makes no request for an event id that is not a UUID", async () => {
    expect((await getObservation("nope")).kind).toBe("not_found");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("derives the echoed horizons: the request's ascending, or the default", () => {
    expect(effectiveHorizons({})).toEqual([1, 5, 20]);
    expect(effectiveHorizons({ horizons: [20, 5] })).toEqual([5, 20]);
  });
});

describe("client: ApiResult mapping and echo", () => {
  const event = captured("benchmark_selected");
  const ask = () => getObservation(event.event_id, { horizons: [1, 2], benchmarkId: BENCHMARK_ID });

  it("a domain refusal inside a 200 stays ok: it is data, not a transport failure", async () => {
    const refused = captured("unknown_timing");
    fetchMock.mockResolvedValue(jsonResponse(refused.response));
    const result = await getObservation(refused.event_id, { horizons: [1, 2], benchmarkId: BENCHMARK_ID });
    expect(result.kind).toBe("ok");
  });

  it.each([
    [404, "not_found"],
    [422, "rejected"],
    [503, "unavailable"],
    [500, "server_error"],
  ] as const)("maps HTTP %s to %s", async (status, kind) => {
    fetchMock.mockResolvedValue(jsonResponse(status === 422 ? CAPTURE.rejected_horizons.body : { detail: "x" }, status));
    expect((await ask()).kind).toBe(kind);
  });

  it("keeps the 422 message about parameters and drops the echoed input", async () => {
    fetchMock.mockResolvedValue(jsonResponse(CAPTURE.rejected_horizons.body, 422));
    const result = await ask();
    expect(result).toMatchObject({ kind: "rejected", issues: [{ where: "query.horizons" }] });
    expect(failureCopy(result as never).body).toMatch(/positive integers/);
  });

  it("a network failure is unreachable and a payload that drifts is a contract failure with no payload in it", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    expect((await ask()).kind).toBe("unreachable");
    const broken = clone(event.response) as unknown as Json;
    broken.descriptive.study.stock_returns.forward_returns[0].return_value = 0.1;
    fetchMock.mockResolvedValue(jsonResponse(broken));
    const result = await ask();
    expect(result.kind).toBe("contract");
    expect(JSON.stringify(result)).not.toContain("0.1");
  });

  it.each([
    ["another event", (r: Json) => (r.identity.event_id = "99999999-9999-4999-8999-999999999999"), "$.identity.event_id"],
    ["other horizons", (r: Json) => (r.parameters.horizons = [1, 3]), "$.parameters.horizons"],
    ["another benchmark", (r: Json) => (r.parameters.benchmark_id = EMPTY_BENCHMARK_ID), "$.parameters.benchmark_id"],
    ["a benchmark nobody asked for", (r: Json) => (r.parameters.benchmark_id = BENCHMARK_ID), null],
    ["a filing nobody asked for", (r: Json) => (r.parameters.filing_accession = "0000320193-24-000010"), "$.parameters.filing_accession"],
    ["a non-split-adjusted basis", (r: Json) => (r.parameters.price_basis = "AS_TRADED"), "$.parameters.price_basis"],
    ["a security nobody asked for", (r: Json) => (r.parameters.security_id = "99999999-9999-4999-8999-999999999999"), "$.parameters.security_id"],
  ])("refuses a response echoing %s instead of showing it under this request", async (_name, mutate, path) => {
    const copy = clone(event.response) as unknown as Json;
    mutate(copy);
    fetchMock.mockResolvedValue(jsonResponse(copy));
    const asked = path === null ? await getObservation(event.event_id, { horizons: [1, 2] }) : await ask();
    expect(asked.kind).toBe("contract");
    if (path !== null) expect(asked).toMatchObject({ path });
  });

  it("refuses a filing request whose selectors are not echoed", async () => {
    const evaluated = captured("filing_evaluated");
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(evaluated.response)));
    const accession = evaluated.params.filing_accession!;
    const ok = await getObservation(evaluated.event_id, { horizons: [1, 2], benchmarkId: BENCHMARK_ID, filingAccession: accession, epsPeriodClass: "QUARTER", epsKind: "DILUTED" });
    expect(ok.kind).toBe("ok");
    const wrong = await getObservation(evaluated.event_id, { horizons: [1, 2], benchmarkId: BENCHMARK_ID, filingAccession: accession, epsPeriodClass: "ANNUAL", epsKind: "DILUTED" });
    expect(wrong).toMatchObject({ kind: "contract", path: "$.parameters.eps_period_class" });
  });

  it("the benchmark index requires its limit and offset to be echoed", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...CAPTURE.benchmarks, limit: 50, offset: 0 }));
    expect(await getBenchmarks({ limit: 200 })).toMatchObject({ kind: "contract", path: "$.limit" });
  });
});
