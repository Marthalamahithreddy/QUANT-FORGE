import { describe, expect, it } from "vitest";

import { DEFAULT_HORIZONS, HORIZON_MAX_COUNT, HORIZON_MAX_VALUE } from "@/lib/api/schema";
import { parseResearchConfig, researchHref } from "@/lib/research-view";

const E = "cb55653a-450a-49cc-9837-49c851a1cb5f";
const B = "3AD6A9D9-5C7D-4F98-B50C-FB1E86FEB583";
const ACC = "0000320193-24-000010";
const parse = (raw: Record<string, string | string[]>) => parseResearchConfig(E, raw);

describe("defaults: nothing is chosen for the reader", () => {
  it("an empty query is the default study: default horizons, no benchmark, no filing", () => {
    const state = parse({});
    expect(state.errors).toEqual([]);
    expect(state.horizons).toEqual([...DEFAULT_HORIZONS]);
    expect(state.horizonsAreDefault).toBe(true);
    expect(state.query).toEqual({ horizons: undefined, benchmarkId: undefined, filingAccession: undefined, epsPeriodClass: undefined, epsKind: undefined });
    expect(state.canonical).toBe(`/research/events/${E}`);
    expect(state.redirect).toBe(false);
  });

  it("never fills a benchmark or filing from anywhere (no persistence, no inference)", () => {
    const state = parse({ horizons: "1,2" });
    expect(state.query.benchmarkId).toBeUndefined();
    expect(state.query.filingAccession).toBeUndefined();
    expect(state.canonical).not.toMatch(/benchmark|filing/);
  });
});

describe("horizons", () => {
  it.each([
    ["1", [1]],
    ["5,1", [1, 5]],
    ["20,5,1,60", [1, 5, 20, 60]],
    ["252", [252]],
    [Array.from({ length: HORIZON_MAX_COUNT }, (_, i) => i + 1).join(","), Array.from({ length: HORIZON_MAX_COUNT }, (_, i) => i + 1)],
  ])("accepts %s as %j, sorted ascending", (text, expected) => {
    const state = parse({ horizons: text });
    expect(state.errors).toEqual([]);
    expect(state.horizons).toEqual(expected);
  });

  it("omits the default from the canonical address, whatever order it was typed in", () => {
    const state = parse({ horizons: "20,1,5" });
    expect(state.horizonsAreDefault).toBe(true);
    expect(state.canonical).toBe(`/research/events/${E}`);
    expect(state.redirect).toBe(true);
    expect(state.query.horizons).toBeUndefined();
  });

  it("writes custom horizons ascending with readable commas", () => {
    const state = parse({ horizons: "5,1" });
    expect(state.canonical).toBe(`/research/events/${E}?horizons=1,5`);
    expect(state.query.horizons).toEqual([1, 5]);
    expect(state.redirect).toBe(true);
    expect(parse({ horizons: "1,5" }).redirect).toBe(false);
  });

  it.each([
    ["zero", "0"],
    ["a negative", "-1"],
    ["above the maximum", String(HORIZON_MAX_VALUE + 1)],
    ["a decimal", "1.5"],
    ["a space", "1, 5"],
    ["leading zero", "01"],
    ["a trailing comma", "1,5,"],
    ["an empty element", "1,,5"],
    ["text", "one"],
    ["too many", Array.from({ length: HORIZON_MAX_COUNT + 1 }, (_, i) => i + 1).join(",")],
  ])("rejects %s locally and does not repair it", (_name, text) => {
    const state = parse({ horizons: text });
    expect(state.errors.map((e) => e.param)).toEqual(["horizons"]);
    expect(state.canonical).toBeNull();
    expect(state.shown.horizons).toBe(text); // kept for the form
  });

  it("rejects duplicates by name, and a repeated parameter", () => {
    expect(parse({ horizons: "5,5" }).errors[0]?.message).toMatch(/repeat/);
    expect(parse({ horizons: ["1", "5"] }).errors.map((e) => e.param)).toContain("horizons");
  });

  it("an empty value is the default and is redirected away", () => {
    const state = parse({ horizons: "" });
    expect(state.errors).toEqual([]);
    expect(state.redirect).toBe(true);
  });

  it("the numeric bounds are the API's", () => {
    expect(HORIZON_MAX_COUNT).toBe(10);
    expect(HORIZON_MAX_VALUE).toBe(252);
  });
});

describe("benchmark", () => {
  it("accepts a UUID and canonicalises it to lower case", () => {
    const state = parse({ benchmark_id: B });
    expect(state.errors).toEqual([]);
    expect(state.query.benchmarkId).toBe(B.toLowerCase());
    expect(state.canonical).toBe(`/research/events/${E}?benchmark_id=${B.toLowerCase()}`);
    expect(state.redirect).toBe(true);
  });

  it.each([["not-a-uuid"], ["0000000000"], ["3ad6a9d9"]])("rejects %s without sending it", (text) => {
    const state = parse({ benchmark_id: text });
    expect(state.errors.map((e) => e.param)).toEqual(["benchmark_id"]);
    expect(state.query.benchmarkId).toBeUndefined();
  });

  it("rejects a repeated benchmark and drops an empty one", () => {
    expect(parse({ benchmark_id: [B, B] }).errors.map((e) => e.param)).toContain("benchmark_id");
    const empty = parse({ benchmark_id: "" });
    expect(empty.errors).toEqual([]);
    expect(empty.redirect).toBe(true);
    expect(empty.query.benchmarkId).toBeUndefined();
  });
});

describe("filing and its selectors", () => {
  it("a filing needs BOTH selectors: neither is inferred, not even from the form type", () => {
    const incomplete: Record<string, string>[] = [{ filing_accession: ACC }, { filing_accession: ACC, eps_kind: "DILUTED" }, { filing_accession: ACC, eps_period_class: "QUARTER" }];
    for (const raw of incomplete) {
      const state = parse(raw);
      expect(state.errors.length).toBe(1);
      expect(state.errors[0]?.message).toMatch(/needs both/);
      expect(state.canonical).toBeNull();
    }
  });

  it("accepts a filing with both selectors, in canonical order", () => {
    const state = parse({ eps_kind: "DILUTED", filing_accession: ACC, eps_period_class: "QUARTER" });
    expect(state.errors).toEqual([]);
    expect(state.query).toMatchObject({ filingAccession: ACC, epsPeriodClass: "QUARTER", epsKind: "DILUTED" });
    expect(state.canonical).toBe(`/research/events/${E}?filing_accession=${ACC}&eps_period_class=QUARTER&eps_kind=DILUTED`);
    expect(state.redirect).toBe(false); // the order of parameters never matters, only their values
  });

  it("selectors without a filing have no effect: they are dropped, never sent", () => {
    const state = parse({ eps_period_class: "QUARTER", eps_kind: "DILUTED" });
    expect(state.errors).toEqual([]);
    expect(state.query.epsPeriodClass).toBeUndefined();
    expect(state.query.epsKind).toBeUndefined();
    expect(state.canonical).toBe(`/research/events/${E}`);
    expect(state.redirect).toBe(true);
  });

  it.each([
    ["a malformed accession", { filing_accession: "000032019324000010", eps_period_class: "QUARTER", eps_kind: "DILUTED" }, "filing_accession"],
    ["a lower-case period class", { filing_accession: ACC, eps_period_class: "quarter", eps_kind: "DILUTED" }, "eps_period_class"],
    ["an unknown EPS kind", { filing_accession: ACC, eps_period_class: "QUARTER", eps_kind: "BASIC" }, "eps_kind"],
    ["a repeated selector", { filing_accession: ACC, eps_period_class: ["QUARTER", "ANNUAL"], eps_kind: "DILUTED" }, "eps_period_class"],
  ])("rejects %s", (_name, raw, param) => {
    expect(parse(raw).errors.map((e) => e.param)).toContain(param);
  });
});

describe("canonical form, back/forward and the benchmark page", () => {
  it("drops unknown parameters and keeps what matters, in a fixed order", () => {
    const state = parse({ utm: "x", benchmark_id: B.toLowerCase(), horizons: "1,5,60" });
    expect(state.canonical).toBe(`/research/events/${E}?horizons=1,5,60&benchmark_id=${B.toLowerCase()}`);
    expect(state.redirect).toBe(true);
  });

  it("an already canonical address is not redirected (so back/forward and refresh are stable)", () => {
    const href = researchHref(E, { horizons: [1, 2], benchmarkId: B.toLowerCase(), filingAccession: ACC, epsPeriodClass: "ANNUAL", epsKind: "BASIC_AND_DILUTED" });
    const params = Object.fromEntries(new URLSearchParams(href.split("?")[1]));
    const state = parse(params);
    expect(state.redirect).toBe(false);
    expect(state.canonical).toBe(href);
  });

  it("the benchmark page is selector position only: it is not part of the study's identity", () => {
    const state = parse({ benchmark_id: B.toLowerCase(), bpage: "3" });
    expect(state.benchmarkPage).toBe(3);
    expect(state.canonical).toContain("bpage=3");
    expect(state.studyKey).toBe(`/research/events/${E}?benchmark_id=${B.toLowerCase()}`);
    expect(parse({ benchmark_id: B.toLowerCase(), bpage: "1" }).redirect).toBe(true);
    expect(parse({ bpage: "0" }).errors.map((e) => e.param)).toEqual(["bpage"]);
    expect(parse({ bpage: "" }).errors).toEqual([]);
  });

  it("round-trips: a built address parses back to the same configuration", () => {
    const query = { horizons: [1, 5, 20, 60], benchmarkId: B.toLowerCase(), filingAccession: ACC, epsPeriodClass: "QUARTER" as const, epsKind: "DILUTED" as const };
    const href = researchHref(E, query);
    const params = Object.fromEntries(new URLSearchParams(href.split("?")[1]));
    expect(parse(params).query).toMatchObject(query);
  });

  it("an invalid configuration yields no request address at all", () => {
    const state = parse({ horizons: "x", benchmark_id: "y" });
    expect(state.canonical).toBeNull();
    expect(state.studyKey).toBeNull();
    expect(state.query.horizons).toBeUndefined();
    expect(state.query.benchmarkId).toBeUndefined();
    expect(state.errors.map((e) => e.param)).toEqual(["horizons", "benchmark_id"]);
  });
});
