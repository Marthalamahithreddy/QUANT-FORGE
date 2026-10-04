import { describe, expect, it } from "vitest";

import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  CHANGE_STATUSES,
  COMPARATIVE_STATUSES,
  EPS_STATUSES,
  FilingsContractError,
  PERCENTAGE_REASONS,
  TRANSITIONS,
  parseFilingEps,
  parseFilingIndexPage,
} from "@/types/filings";
import {
  CHANGE_STATUS_LABEL,
  COMPARATIVE_STATUS_LABEL,
  EPS_STATUS_LABEL,
  PERCENTAGE_REASON_DETAIL,
  TRANSITION_LABEL,
  filingDetailHref,
  navigationPeriodClass,
  readSelector,
  isPeriodClass,
} from "@/lib/filings-view";
import { buildFilingEpsPath, buildFilingsPath } from "@/lib/api/filings";
import { FILING_FORMS } from "@/types/filings";
import { filingsPage, makeEps, makeFact, makeFiling } from "./filings-fixtures";

type Json = Record<string, unknown>;
const withChange = (change: Json) =>
  makeEps({ change: { ...(makeEps().change as Json), ...change } });

describe("filings index parser", () => {
  it("parses rows with every nullable field, keeping strings and dates as sent", () => {
    const page = parseFilingIndexPage(
      filingsPage([
        makeFiling({
          company_cik: null,
          report_date: null,
          fsds_period_end: null,
        }),
      ]),
    );
    expect(page.items[0]).toEqual({
      accession: "0000320193-24-000010",
      company_cik: null,
      form: "10-Q",
      filing_date: "2024-05-03",
      report_date: null,
      fsds_period_end: null,
    });
    expect(page.total).toBe(1);
  });

  it.each([
    ["unknown form", { form: "S-1" }],
    ["lower-case form", { form: "10-q" }],
    ["malformed accession", { accession: "000032019324000010" }],
    ["short CIK", { company_cik: "320193" }],
    ["timestamp instead of a date", { filing_date: "2024-05-03T00:00:00Z" }],
    ["non-date report date", { report_date: "yesterday" }],
    ["numeric period end", { fsds_period_end: 20240331 }],
  ])("fails loudly on %s", (_name, override) => {
    expect(() =>
      parseFilingIndexPage(filingsPage([{ ...makeFiling(), ...override }])),
    ).toThrow(FilingsContractError);
  });

  it("rejects a malformed envelope", () => {
    expect(() =>
      parseFilingIndexPage({ items: [], total: -1, limit: 50, offset: 0 }),
    ).toThrow(FilingsContractError);
    expect(() =>
      parseFilingIndexPage({ items: {}, total: 0, limit: 50, offset: 0 }),
    ).toThrow(FilingsContractError);
  });

  it("does not de-duplicate a joint accession", () => {
    const page = parseFilingIndexPage(
      filingsPage([makeFiling({ company_cik: "0000555555" }), makeFiling()]),
    );
    expect(page.items.map((i) => i.company_cik)).toEqual([
      "0000555555",
      "0000320193",
    ]);
  });
});

describe("filing EPS parser", () => {
  it("parses an AVAILABLE chain and keeps every financial value a string", () => {
    const eps = parseFilingEps(makeEps());
    expect(eps.current.fact?.value).toBe("1.5300");
    expect(eps.change.eps_change).toBe("0.5100");
    expect(eps.change.percentage_change).toBe("0.5");
    for (const v of [
      eps.current.fact?.value,
      eps.comparative.fact?.value,
      eps.change.eps_change,
    ]) {
      expect(typeof v).toBe("string");
    }
  });

  it("accepts negative, zero, tiny and 50-digit values exactly", () => {
    const ratio = `0.${"0".repeat(49)}1`; // 50 fractional digits
    const eps = parseFilingEps(
      makeEps({
        current: {
          status: "AVAILABLE",
          reason: "r",
          fact: makeFact({ value: "-0.5000" }),
        },
        change: {
          ...(makeEps().change as Json),
          eps_change: "0.0000001",
          percentage_change: ratio,
        },
      }),
    );
    expect(eps.current.fact?.value).toBe("-0.5000");
    expect(eps.change.eps_change).toBe("0.0000001");
    expect(eps.change.percentage_change).toBe(ratio);
    expect(eps.change.percentage_change?.split(".")[1]).toHaveLength(50);
  });

  it.each([
    ["a JSON number value", makeFact({ value: 1.53 })],
    ["exponent notation", makeFact({ value: "1E-7" })],
    ["NaN text", makeFact({ value: "NaN" })],
    ["a padded value", makeFact({ value: " 1.53" })],
  ])("rejects %s as a financial value", (_name, fact) => {
    expect(() =>
      parseFilingEps(
        makeEps({ current: { status: "AVAILABLE", reason: "r", fact } }),
      ),
    ).toThrow(FilingsContractError);
  });

  it.each([
    [
      "current status",
      makeEps({ current: { status: "WEIRD", reason: "r", fact: null } }),
    ],
    [
      "comparative status",
      makeEps({
        comparative: {
          status: "NEW_STATE",
          reason: "r",
          meaning: "m",
          fact: null,
        },
      }),
    ],
    ["change status", withChange({ status: "SOMETHING" })],
    ["transition", withChange({ transition: "MOON_SHOT" })],
    [
      "percentage reason",
      withChange({
        percentage_change: null,
        percentage_unavailable_reason: "WHY",
      }),
    ],
    ["period class", makeEps({ period_class: "YTD" })],
    ["eps kind", makeEps({ eps_kind: "BASIC" })],
    ["form", makeEps({ form: "S-1" })],
    ["a short registrant CIK", makeEps({ company_cik: "320193" })],
    ["a non-string registrant CIK", makeEps({ company_cik: 320193 })],
  ])("fails loudly on an unknown %s", (_name, payload) => {
    expect(() => parseFilingEps(payload)).toThrow(FilingsContractError);
  });

  it.each([
    [
      "AVAILABLE current with no fact",
      makeEps({ current: { status: "AVAILABLE", reason: "r", fact: null } }),
    ],
    [
      "AVAILABLE current with a null value",
      makeEps({
        current: {
          status: "AVAILABLE",
          reason: "r",
          fact: makeFact({ value: null }),
        },
      }),
    ],
    ["AVAILABLE change with no value", withChange({ eps_change: null })],
    [
      "INPUT_UNAVAILABLE change carrying a value",
      withChange({ status: "INPUT_UNAVAILABLE" }),
    ],
    [
      "a percentage and a reason together",
      withChange({ percentage_unavailable_reason: "ZERO_COMPARATIVE" }),
    ],
    [
      "a non-ratio percentage semantic",
      withChange({ percentage_semantics: "percent" }),
    ],
    ["a cleared boundary flag", makeEps({ no_point_in_time_claim: false })],
  ])("rejects an inconsistent payload: %s", (_name, payload) => {
    expect(() => parseFilingEps(payload)).toThrow(FilingsContractError);
  });

  it.each([
    [
      "a fact under a refused current status",
      makeEps({
        current: { status: "WRONG_UNIT", reason: "r", fact: makeFact() },
      }),
    ],
    [
      "a valued fact under NULL_VALUE",
      makeEps({
        current: { status: "NULL_VALUE", reason: "r", fact: makeFact() },
      }),
    ],
    [
      "NULL_VALUE with no fact",
      makeEps({ current: { status: "NULL_VALUE", reason: "r", fact: null } }),
    ],
    [
      "a fact under a refused comparative status",
      makeEps({
        comparative: {
          status: "COMPARATIVE_WRONG_UNIT",
          reason: "r",
          meaning: "m",
          fact: makeFact(),
        },
      }),
    ],
  ])(
    "rejects %s (only AVAILABLE and the null-value status carry a fact)",
    (_name, payload) => {
      expect(() => parseFilingEps(payload)).toThrow(FilingsContractError);
    },
  );

  it("accepts a null-valued comparative fact with its own status", () => {
    const eps = parseFilingEps(
      makeEps({
        comparative: {
          status: "COMPARATIVE_NULL_VALUE",
          reason: "r",
          meaning: "m",
          fact: makeFact({ value: null }),
        },
        change: {
          status: "INPUT_UNAVAILABLE",
          eps_change: null,
          transition: null,
          percentage_change: null,
          percentage_semantics: "ratio",
          percentage_unavailable_reason: null,
        },
      }),
    );
    expect(eps.comparative.fact?.value).toBeNull();
  });
});

describe("explicit wording maps", () => {
  it("label every backend status, transition and reason (13 transitions)", () => {
    expect(TRANSITIONS).toHaveLength(13);
    expect(Object.keys(TRANSITION_LABEL).sort()).toEqual(
      [...TRANSITIONS].sort(),
    );
    expect(Object.keys(EPS_STATUS_LABEL).sort()).toEqual(
      [...EPS_STATUSES].sort(),
    );
    expect(Object.keys(COMPARATIVE_STATUS_LABEL).sort()).toEqual(
      [...COMPARATIVE_STATUSES].sort(),
    );
    expect(Object.keys(CHANGE_STATUS_LABEL).sort()).toEqual(
      [...CHANGE_STATUSES].sort(),
    );
    expect(Object.keys(PERCENTAGE_REASON_DETAIL).sort()).toEqual(
      [...PERCENTAGE_REASONS].sort(),
    );
  });

  it("uses neutral wording: no trading language and no good/bad framing", () => {
    const text = Object.values(TRANSITION_LABEL).join(" ");
    expect(text).not.toMatch(
      /bullish|bearish|\bbeat\b|\bmiss\b|\bbuy\b|\bsell\b|signal|strong|weak|good|bad/i,
    );
  });
});

describe("navigation helpers", () => {
  it.each([
    ["10-Q", "QUARTER"],
    ["10-Q/A", "QUARTER"],
    ["10-K", "ANNUAL"],
    ["10-K/A", "ANNUAL"],
    ["8-K", null],
    ["8-K/A", null],
  ] as const)(
    "pre-selects %s as %s for the list link only",
    (form, expected) => {
      expect(navigationPeriodClass(form)).toBe(expected);
    },
  );

  it("builds an encoded link with explicit selectors", () => {
    expect(
      filingDetailHref({
        accession: "0000320193-24-000010",
        form: "10-Q/A",
        company_cik: null,
      }),
    ).toBe(
      "/filings/0000320193-24-000010?period_class=QUARTER&eps_kind=DILUTED",
    );
    expect(
      filingDetailHref({
        accession: "0000320193-24-000010",
        form: "8-K",
        company_cik: null,
      }),
    ).toBe("/filings/0000320193-24-000010?eps_kind=DILUTED");
    expect(
      filingDetailHref({ accession: "a b/c", form: "10-K", company_cik: null }),
    ).toContain("/filings/a%20b%2Fc?");
  });

  it("carries the registrant CIK, leading zeros intact, only when the row has one", () => {
    expect(
      filingDetailHref({
        accession: "0000320193-24-000010",
        form: "10-Q",
        company_cik: "0000012345",
      }),
    ).toBe(
      "/filings/0000320193-24-000010?cik=0000012345&period_class=QUARTER&eps_kind=DILUTED",
    );
    const a = filingDetailHref({
      accession: "0000320193-24-000010",
      form: "10-Q",
      company_cik: "0000320193",
    });
    const b = filingDetailHref({
      accession: "0000320193-24-000010",
      form: "10-Q",
      company_cik: "0000555555",
    });
    expect(a).not.toBe(b);
    expect(a).toContain("cik=0000320193");
    expect(b).toContain("cik=0000555555");
    // a CIK-less row stays accession-only: nothing is invented
    expect(
      filingDetailHref({
        accession: "0000320193-24-000010",
        form: "10-Q",
        company_cik: null,
      }),
    ).not.toContain("cik=");
  });

  it("sends the CIK to the API verbatim, and only when qualified", () => {
    expect(
      buildFilingEpsPath(
        "0000320193-24-000010",
        "QUARTER",
        "DILUTED",
        "0000012345",
      ),
    ).toBe(
      "/api/v1/filings/0000320193-24-000010/eps?period_class=QUARTER&eps_kind=DILUTED&cik=0000012345",
    );
    expect(
      buildFilingEpsPath("0000320193-24-000010", "QUARTER", "DILUTED"),
    ).not.toContain("cik");
  });

  it("encodes API paths and never builds from raw fragments", () => {
    expect(buildFilingEpsPath("a b/c", "ANNUAL", "BASIC_AND_DILUTED")).toBe(
      "/api/v1/filings/a%20b%2Fc/eps?period_class=ANNUAL&eps_kind=BASIC_AND_DILUTED",
    );
    expect(
      buildFilingsPath({ form: "10-Q/A", cik: "0000320193", offset: 50 }),
    ).toBe("/api/v1/filings?form=10-Q%2FA&cik=0000320193&limit=50&offset=50");
  });

  it("reads selectors strictly without coercion", () => {
    expect(readSelector(undefined, isPeriodClass)).toEqual({
      state: "missing",
    });
    expect(readSelector("", isPeriodClass)).toEqual({ state: "missing" });
    expect(readSelector("QUARTER", isPeriodClass)).toEqual({
      state: "ok",
      value: "QUARTER",
    });
    expect(readSelector("quarter", isPeriodClass)).toEqual({
      state: "invalid",
      raw: "quarter",
    });
    expect(readSelector(["QUARTER", "ANNUAL"], isPeriodClass).state).toBe(
      "invalid",
    );
  });

  it("offers every backend form exactly", () => {
    expect([...FILING_FORMS]).toEqual([
      "10-K",
      "10-K/A",
      "10-Q",
      "10-Q/A",
      "8-K",
      "8-K/A",
    ]);
  });
});

describe("financial decimals never pass through JS numbers", () => {
  const files = [
    "src/types/filings.ts",
    "src/lib/filings-view.ts",
    "src/lib/api/filings.ts",
    "src/components/filings/eps-panels.tsx",
    "src/components/filings/filings-table.tsx",
    "src/app/filings/[accession]/page.tsx",
  ];
  it.each(files)("%s has no float or Number conversion", (file) => {
    const source = readFileSync(join(process.cwd(), file), "utf8")
      // comments and strings may mention the words; only code matters
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    expect(source).not.toMatch(
      /parseFloat|\bNumber\(|Number\.parse|\bparseInt\(|\bMath\.|\bBigDecimal/,
    );
  });
});
