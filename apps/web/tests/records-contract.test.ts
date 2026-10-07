/* eslint-disable @typescript-eslint/no-explicit-any -- tests mutate deep, deliberately untyped payloads to model drift */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OBSERVATION_TIMEOUT_MS, READ_TIMEOUT_MS } from "@/lib/api/client";
import { decode } from "@/lib/api/decode";
import { decodeRecordDetail, decodeRecordPage, decodeVerification } from "@/lib/api/decoders/records";
import { failureCopy } from "@/lib/api/messages";
import { buildRecordListPath, getRecord, getRecordPage, verifyRecord } from "@/lib/api/records";
import { RECORD_PAGE_SIZE } from "@/lib/api/schema";
import { storedEpsPanels, storedStudyToStudy } from "@/lib/stored-result";
import { jsonResponse } from "./fixtures";
import { RECORDS, clone, record, recordDetail, recordVerify } from "./records-fixtures";

type Json = Record<string, any>;
const detail = (value: unknown) => decodeRecordDetail(value, "$");
const page = (value: unknown) => decodeRecordPage(value, "$");
const verification = (value: unknown) => decodeVerification(value, "$");
const ok = (name: string) => clone(recordDetail(name));

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("Step 12B: saved records and the proven-target-without-price status", () => {
  const stockReturns = (stored: Json): Json[] => stored.result.descriptive.study.stock_returns.forward_returns;

  it("decodes a stored result whose horizon has a proven target session and no stored price", () => {
    const stored = ok("complete_plain");
    Object.assign(stockReturns(stored)[1]!, { status: "MISSING_TARGET_PRICE", target_session: "2026-03-09", target_close: null, return_value: null });
    expect(() => detail(stored)).not.toThrow();
  });

  it("still fails loudly on a status the contract does not know", () => {
    const stored = ok("complete_plain");
    stockReturns(stored)[1]!.status = "TARGET_NOT_YET_OBSERVABLE";
    expect(() => detail(stored)).toThrow();
  });
});

describe("pinned backend records", () => {
  const detailCases = RECORDS.records.filter((entry) => entry.detail.status === 200);
  const verifyCases = RECORDS.records.filter((entry) => entry.verify?.status === 200);

  it.each(detailCases.map((entry) => [entry.name, entry] as const))("the detail decoder accepts the real record: %s", (_name, entry) => {
    expect(() => detail(entry.detail.body)).not.toThrow();
  });

  it.each(verifyCases.map((entry) => [entry.name, entry] as const))("the verification decoder accepts the real report: %s", (_name, entry) => {
    expect(() => verification(entry.verify!.body)).not.toThrow();
  });

  it.each(Object.entries(RECORDS.lists).filter(([, reply]) => reply.status === 200))("the list decoder accepts the real page: %s", (_name, reply) => {
    expect(() => page(reply.body)).not.toThrow();
  });

  it("the empty store decodes to an empty page", () => {
    expect(page(RECORDS.empty_list.body)).toMatchObject({ items: [], total: 0 });
  });

  it("covers every state the Saved Research surface must show", () => {
    const levels = new Set(detailCases.map((entry) => entry.detail.body!.reproducibility_level));
    expect(levels).toEqual(new Set(["COMPLETE", "PARTIAL"]));
    expect(new Set(verifyCases.map((entry) => entry.verify!.body!.status))).toEqual(
      new Set(["MATCH", "EVIDENCE_INCOMPLETE", "MANIFEST_CHANGED", "RESULT_CHANGED"]),
    );
    const gaps = new Set(detailCases.flatMap((entry) => entry.detail.body!.reproducibility_gaps as string[]));
    expect(gaps).toEqual(
      new Set(["EVENT_WITHOUT_INGESTION_RUN", "STOCK_BAR_WITHOUT_INGESTION_RUN", "BENCHMARK_BARS_WITHOUT_INGESTION_RUN", "FILING_WITHOUT_INGESTION_RUN", "ACCOUNTING_NOT_INGESTED"]),
    );
    expect(record("event_missing").verify!.status).toBe(409);
    expect(record("corrupt_manifest").verify!.status).toBe(500);
    expect(RECORDS.unknown_record.detail.status).toBe(404);
    const results = detailCases.map((entry) => entry.detail.body!.result);
    expect(results.some((result) => result.point_in_time.study !== null)).toBe(true);
    expect(new Set(results.map((result) => result.accounting.state))).toContain("EVALUATED");
    expect(new Set(results.map((result) => result.accounting.change?.status))).toContain("INPUT_UNAVAILABLE");
  });

  it("every stored financial value in every captured record is an exact string", () => {
    const FINANCIAL = new Set(["close", "return_value", "anchor_close", "target_close", "benchmark_return", "abnormal_return", "absolute_surprise", "surprise_ratio", "reported_eps", "estimated_eps", "eps_change", "percentage_change", "value"]);
    const walk = (node: unknown, key = ""): void => {
      if (Array.isArray(node)) return node.forEach((item) => walk(item, key));
      if (node !== null && typeof node === "object") return Object.entries(node).forEach(([k, v]) => walk(v, k));
      if (FINANCIAL.has(key) && node !== null) expect(typeof node, key).toBe("string");
    };
    for (const entry of detailCases) walk(entry.detail.body);
    // The decoder keeps the very strings: the decoded value serialises back to the same figures.
    const decoded = detail(recordDetail("complete_filing_accounting")) as any;
    expect(decoded.manifest.evidence.stock.bars[0].close).toBe(recordDetail("complete_filing_accounting").manifest.evidence.stock.bars[0].close);
    expect(typeof decoded.result.accounting.change.percentage_change).toBe("string");
  });
});

describe("the list decoder", () => {
  const first = () => clone(RECORDS.lists.limit_3_offset_0!.body) as Json;

  it("rejects a JSON number or padded text where a fingerprint, a level or a count belongs", () => {
    const bad = first();
    bad.items[0].manifest_fingerprint = "abc";
    expect(() => page(bad)).toThrow();
    const unknownLevel = first();
    unknownLevel.items[0].reproducibility_level = "UNKNOWN";
    expect(() => page(unknownLevel)).toThrow();
    const unknownGap = first();
    unknownGap.items[0].reproducibility_gaps = ["SOMETHING_NEW"];
    unknownGap.items[0].reproducibility_level = "PARTIAL";
    expect(() => page(unknownGap)).toThrow();
    const algorithm = first();
    algorithm.items[0].hash_algorithm = "md5";
    expect(() => page(algorithm)).toThrow();
  });

  it("keeps null and absent apart in the stored request", () => {
    const nulled = first();
    nulled.items[0].request.benchmark_id = null;
    expect(() => page(nulled)).not.toThrow();
    const absent = first();
    delete absent.items[0].request.benchmark_id;
    expect(() => page(absent)).toThrow();
  });

  it("enforces the documented level/gaps rule: COMPLETE has no gaps and PARTIAL has some", () => {
    const completeWithGap = first();
    completeWithGap.items[0].reproducibility_level = "COMPLETE";
    completeWithGap.items[0].reproducibility_gaps = ["EVENT_WITHOUT_INGESTION_RUN"];
    expect(() => page(completeWithGap)).toThrow();
    const partialWithout = first();
    partialWithout.items[0].reproducibility_level = "PARTIAL";
    partialWithout.items[0].reproducibility_gaps = [];
    expect(() => page(partialWithout)).toThrow();
  });

  it("refuses more items than the page limit", () => {
    const over = first();
    over.limit = 1;
    expect(() => page(over)).toThrow();
  });
});

describe("the detail decoder", () => {
  it("rejects an unknown enum member anywhere in the stored manifest or result", () => {
    const cases: [string, (r: Json) => void][] = [
      ["evidence state", (r) => (r.manifest.evidence.stock.state = "MAYBE")],
      ["price basis", (r) => (r.manifest.request.price_basis = "ADJUSTED")],
      ["listing status", (r) => (r.manifest.evidence.listing_resolution_status = "GUESSED")],
      ["forward status", (r) => (r.result.descriptive.study.stock_returns.forward_returns[0].status = "WEIRD")],
      ["accounting state", (r) => (r.result.accounting.state = "SORT_OF")],
      ["eligibility", (r) => (r.result.point_in_time.eligibility = "MAYBE")],
    ];
    for (const [label, mutate] of cases) {
      const r = ok("complete_filing_accounting");
      mutate(r);
      expect(() => detail(r), label).toThrow();
    }
  });

  it("rejects a financial value that is a JSON number, has an exponent or padding", () => {
    for (const bad of [1.53, "1.5E+2", " 1.53", "1,53"]) {
      const r = ok("complete_filing_accounting");
      r.manifest.evidence.stock.bars[0].close = bad;
      expect(() => detail(r), String(bad)).toThrow();
      const s = ok("complete_filing_accounting");
      s.result.event.reported_eps = bad;
      expect(() => detail(s), `result ${String(bad)}`).toThrow();
    }
  });

  it("keeps null and absent apart inside the manifest and the result", () => {
    const nulled = ok("complete_plain");
    nulled.manifest.evidence.event.ingestion_run_id = null;
    expect(() => detail(nulled)).not.toThrow();
    const absent = ok("complete_plain");
    delete absent.manifest.evidence.event.ingestion_run_id;
    expect(() => detail(absent)).toThrow();
    const noSection = ok("complete_plain");
    delete noSection.manifest.evidence.pit;
    expect(() => detail(noSection)).toThrow();
    const noStudy = ok("complete_plain");
    delete noStudy.result.descriptive.study;
    expect(() => detail(noStudy)).toThrow();
  });

  it("enforces the stored invariants: the PIT study exactly when eligible, the EPS change exactly when evaluated", () => {
    const eligibleWithoutStudy = ok("complete_pit_eligible");
    eligibleWithoutStudy.result.point_in_time.study = null;
    expect(() => detail(eligibleWithoutStudy)).toThrow();
    const refusedWithStudy = ok("complete_plain");
    refusedWithStudy.result.point_in_time.study = ok("complete_pit_eligible").result.point_in_time.study;
    expect(() => detail(refusedWithStudy)).toThrow();
    const evaluatedWithoutChange = ok("complete_filing_accounting");
    evaluatedWithoutChange.result.accounting.change = null;
    expect(() => detail(evaluatedWithoutChange)).toThrow();
    const notRequestedWithChange = ok("complete_plain");
    notRequestedWithChange.result.accounting.change = ok("complete_filing_accounting").result.accounting.change;
    expect(() => detail(notRequestedWithChange)).toThrow();
  });

  it("holds a stored study to the live per-horizon standard (same horizons, a figure exactly when AVAILABLE)", () => {
    const reordered = ok("complete_benchmark");
    reordered.result.descriptive.study.benchmark_returns.reverse();
    expect(() => detail(reordered)).toThrow();
    const figureWithoutStatus = ok("complete_benchmark");
    figureWithoutStatus.result.descriptive.study.stock_returns.forward_returns[0].status = "NO_ALIGNMENT";
    expect(() => detail(figureWithoutStatus)).toThrow();
  });

  it("requires the head request to equal the stored manifest's request, and the result to describe that event", () => {
    const differentHorizons = ok("complete_plain");
    differentHorizons.request.horizons = [1];
    expect(() => detail(differentHorizons)).toThrow();
    const differentBenchmark = ok("complete_benchmark");
    differentBenchmark.request.benchmark_id = null;
    expect(() => detail(differentBenchmark)).toThrow();
    const otherEvent = ok("complete_plain");
    otherEvent.result.event.event_id = "11111111-1111-4111-8111-111111111111";
    expect(() => detail(otherEvent)).toThrow();
  });

  it("refuses a stored fact that carries part of its identity", () => {
    const r = ok("complete_filing_accounting");
    r.result.accounting.change.comparative.current.tag = null;
    expect(() => detail(r)).toThrow();
  });

  it("reports the path and what was expected, never the received value", () => {
    const r = ok("complete_plain");
    r.manifest.evidence.stock.bars[0].close = "SECRET-12345";
    const outcome = decode(decodeRecordDetail, r);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.path).toBe("$.manifest.evidence.stock.bars[0].close");
      expect(JSON.stringify(outcome)).not.toContain("SECRET");
    }
  });
});

describe("the verification decoder", () => {
  const report = (name: string) => clone(recordVerify(name)) as Json;

  it("reads the status the API returned and never derives one", () => {
    for (const name of ["complete_plain", "partial_benchmark", "manifest_changed", "result_changed"]) {
      expect((verification(report(name)) as any).status).toBe(recordVerify(name).status);
    }
  });

  it("rejects an unknown status, baseline or section: there is no generic fallback", () => {
    for (const mutate of [(r: Json) => (r.status = "UNKNOWN"), (r: Json) => (r.baseline = "STALE"), (r: Json) => (r.changed_sections = ["everything"])]) {
      const r = report("manifest_changed");
      mutate(r);
      expect(() => verification(r)).toThrow();
    }
  });

  it("requires every key, and null is not an empty list", () => {
    for (const key of ["record_id", "status", "baseline", "reproducibility_level", "changed_sections", "changed_paths", "changed_result_paths", "expected", "actual"]) {
      const r = report("complete_plain");
      delete r[key];
      expect(() => verification(r), key).toThrow();
    }
    const nulled = report("complete_plain");
    nulled.changed_paths = null;
    expect(() => verification(nulled)).toThrow();
  });

  it("refuses a report whose parts contradict its status (drift), and shows a changed status with no paths as it came", () => {
    const matchWithPaths = report("complete_plain");
    matchWithPaths.changed_result_paths = ["event.reported_eps"];
    expect(() => verification(matchWithPaths)).toThrow();
    const matchWithManifestPaths = report("complete_plain");
    matchWithManifestPaths.changed_paths = ["evidence.stock"];
    expect(() => verification(matchWithManifestPaths)).toThrow();
    const resultChangedWithManifestPaths = report("result_changed");
    resultChangedWithManifestPaths.changed_paths = ["evidence.stock"];
    expect(() => verification(resultChangedWithManifestPaths)).toThrow();
    const incompleteWithPaths = report("partial_benchmark");
    incompleteWithPaths.changed_paths = ["evidence.stock"];
    expect(() => verification(incompleteWithPaths)).toThrow();
    // The verifier's own answer is not discarded because a list came back empty.
    const manifestChangedWithoutPaths = report("manifest_changed");
    manifestChangedWithoutPaths.changed_paths = [];
    expect((verification(manifestChangedWithoutPaths) as any).status).toBe("MANIFEST_CHANGED");
    const resultChangedWithoutPaths = report("result_changed");
    resultChangedWithoutPaths.changed_result_paths = [];
    expect((verification(resultChangedWithoutPaths) as any).status).toBe("RESULT_CHANGED");
  });

  it("requires the rerun level to be the actual side's, and every side's level to match its gaps", () => {
    const r = report("partial_benchmark");
    r.reproducibility_level = "COMPLETE";
    expect(() => verification(r)).toThrow();
    const sideMismatch = report("partial_benchmark");
    sideMismatch.expected.reproducibility_gaps = [];
    expect(() => verification(sideMismatch)).toThrow();
  });

  it("requires all five fingerprints on each side", () => {
    for (const key of ["request", "methodology", "evidence", "manifest", "result"]) {
      const r = report("complete_plain");
      delete r.actual.fingerprints[key];
      expect(() => verification(r), key).toThrow();
    }
  });

  it("carries the backend's paths unchanged and in order", () => {
    const decoded = verification(report("manifest_changed")) as any;
    expect(decoded.changed_paths).toEqual(recordVerify("manifest_changed").changed_paths);
    expect(decoded.changed_result_paths).toEqual(recordVerify("manifest_changed").changed_result_paths);
    expect(decoded.changed_sections).toEqual(["evidence"]);
  });
});

describe("the stored-result adapters (restructuring only)", () => {
  it("a stored study keeps every figure as the stored string, and the abnormal return is the backend's", () => {
    const stored = (detail(ok("complete_benchmark")) as any).result.descriptive.study;
    const study = storedStudyToStudy(stored);
    expect(study.stock_returns.forward_returns.map((r) => r.return_value)).toEqual(stored.stock_returns.forward_returns.map((r: any) => r.return_value));
    expect(study.benchmark.returns.map((r) => r.benchmark_return)).toEqual(stored.benchmark_returns.map((r: any) => r.benchmark_return));
    expect(study.abnormal_returns.map((r) => r.abnormal_return)).toEqual(stored.abnormal_returns.map((r: any) => r.abnormal_return));
    // Doctoring the stored abnormal return changes the adapted figure and nothing recomputes it.
    stored.abnormal_returns[0].abnormal_return = "0.9";
    expect(storedStudyToStudy(stored).abnormal_returns[0]!.abnormal_return).toBe("0.9");
  });

  it("the EPS panels carry the stored values and the stored refusal statuses", () => {
    const evaluated = (detail(ok("complete_filing_accounting")) as any).result.accounting.change;
    const panels = storedEpsPanels(evaluated);
    expect(panels.current.fact?.value).toBe("1.5300");
    expect(panels.comparative.fact?.value).toBe("1.5200");
    expect(panels.change).toMatchObject({ eps_change: "0.0100", percentage_semantics: "ratio", status: "AVAILABLE" });
    const unavailable = (detail(ok("partial_accounting_not_ingested")) as any).result.accounting.change;
    const refused = storedEpsPanels(unavailable);
    expect(refused.current).toMatchObject({ status: "NOT_INGESTED", fact: null });
    expect(refused.comparative.status).toBe("CURRENT_UNAVAILABLE");
    expect(refused.change).toMatchObject({ status: "INPUT_UNAVAILABLE", eps_change: null });
  });
});

describe("the record client", () => {
  const first = record("complete_plain");

  it("lists with limit 20 and the offset it was asked for, and nothing else", async () => {
    expect(buildRecordListPath({ offset: 40 })).toBe(`/api/v1/research/records?limit=${RECORD_PAGE_SIZE}&offset=40`);
    expect(RECORD_PAGE_SIZE).toBe(20);
    const body = { ...(RECORDS.lists.limit_20!.body as Json), limit: 20, offset: 0 };
    fetchMock.mockResolvedValueOnce(jsonResponse(body));
    const result = await getRecordPage({ offset: 0 });
    expect(result.kind).toBe("ok");
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/\/api\/v1\/research\/records\?limit=20&offset=0$/);
  });

  it("refuses a page whose echoed paging is not the request's", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...(RECORDS.lists.limit_20!.body as Json), limit: 50 }));
    expect((await getRecordPage({ offset: 0 })).kind).toBe("contract");
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...(RECORDS.lists.limit_20!.body as Json), offset: 20 }));
    expect((await getRecordPage({ offset: 0 })).kind).toBe("contract");
  });

  it("makes no request for a malformed id and treats a 404 as 'no such record'", async () => {
    expect(await getRecord("not-a-uuid")).toEqual({ kind: "not_found" });
    expect(await verifyRecord("not-a-uuid")).toEqual({ kind: "not_found" });
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Research record not found." }, 404));
    expect(await getRecord(RECORDS.unknown_record.record_id)).toEqual({ kind: "not_found" });
  });

  it("refuses a record or verification for another id (it is not the answer to this question)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(recordDetail("complete_benchmark")));
    expect((await getRecord(first.record_id)).kind).toBe("contract");
    fetchMock.mockResolvedValueOnce(jsonResponse(recordVerify("complete_benchmark")));
    expect((await verifyRecord(first.record_id)).kind).toBe("contract");
  });

  it("maps 409, 500 and 503 to failures, never to a verification status", async () => {
    for (const [status, kind] of [[409, "conflict"], [500, "server_error"], [503, "unavailable"]] as const) {
      fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "internal details that must never show" }, status));
      const result = await verifyRecord(first.record_id);
      expect(result.kind).toBe(kind);
      expect(JSON.stringify(result)).not.toContain("internal details");
    }
  });

  it("the verification uses the 10 s computed class; the list and the record keep the 4 s read class", async () => {
    const spy = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({ detail: "x" }, 404)));
    await getRecordPage();
    await getRecord(first.record_id);
    await verifyRecord(first.record_id);
    expect(spy.mock.calls.map((call) => call[0])).toEqual([READ_TIMEOUT_MS, READ_TIMEOUT_MS, OBSERVATION_TIMEOUT_MS]);
    spy.mockRestore();
  });

  it("only ever issues GET requests with no body", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({ detail: "x" }, 404)));
    await getRecordPage();
    await getRecord(first.record_id);
    await verifyRecord(first.record_id);
    for (const [, init] of fetchMock.mock.calls) {
      expect((init as RequestInit).method).toBe("GET");
      expect((init as RequestInit).body).toBeUndefined();
    }
  });

  it("a corrupt-record 500 yields safe wording with no response text", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(record("corrupt_manifest").detail_after!.body, 500));
    const result = await getRecord(first.record_id);
    expect(result.kind).toBe("server_error");
    if (result.kind !== "ok") {
      const copy = failureCopy(result);
      expect(`${copy.title} ${copy.body}`).not.toMatch(/integrity|canonical|manifest|hash/i);
    }
  });
});
