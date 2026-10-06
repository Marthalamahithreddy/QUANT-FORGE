import { describe, expect, it } from "vitest";

import {
  ContractError,
  arrayOf,
  boolean,
  decimalString,
  decode,
  integer,
  isoDate,
  isoInstant,
  literal,
  matching,
  nonEmptyString,
  nonNegativeInteger,
  nullable,
  object,
  oneOf,
  optional,
  recordOf,
  refine,
  reject,
  string,
  uuid,
} from "@/lib/api/decode";

const run = <T,>(decoder: (value: unknown, path: string) => T, value: unknown) => decoder(value, "$");

describe("object: required keys, null versus absent, extras", () => {
  const shape = object<{ a: string; b: string | null }>({ a: string, b: nullable(string) });

  it("accepts null where the contract allows it", () => {
    expect(run(shape, { a: "x", b: null })).toEqual({ a: "x", b: null });
  });

  it("rejects an ABSENT key even when null would have been fine: absent is contract drift", () => {
    expect(() => run(shape, { a: "x" })).toThrow(ContractError);
    expect(decode(shape, { a: "x" })).toEqual({
      ok: false,
      path: "$.b",
      expected: "the key to be present (null is allowed only where documented)",
    });
  });

  it("rejects an explicit undefined-like value the same way (nothing is coerced to null)", () => {
    expect(() => run(shape, { a: "x", b: undefined })).toThrow(ContractError);
  });

  it("ignores unknown extra keys and does not copy them", () => {
    const decoded = run(shape, { a: "x", b: null, extra: 1, nested: { y: 2 } });
    expect(decoded).toEqual({ a: "x", b: null });
    expect(Object.keys(decoded)).toEqual(["a", "b"]);
  });

  it("returns a new object, never the received one", () => {
    const received = { a: "x", b: null };
    expect(run(shape, received)).not.toBe(received);
  });

  it.each([[null], [[]], ["x"], [1]])("rejects %j as a non-object", (value) => {
    expect(() => run(shape, value)).toThrow(ContractError);
  });

  it("allows only declared optional keys to be absent", () => {
    const withOptional = object<{ a: string; note?: string | undefined }>({ a: string, note: optional(string) });
    expect(run(withOptional, { a: "x" })).toEqual({ a: "x" });
    expect(run(withOptional, { a: "x", note: "n" })).toEqual({ a: "x", note: "n" });
    expect(() => run(withOptional, { a: "x", note: 1 })).toThrow(ContractError);
  });
});

describe("decimalString: exact strings only", () => {
  it.each(["0", "1.5300", "-0.00", "0.0000001", "123456789012345678901234567890.123456789012345678901234567890"])(
    "accepts %s and returns it untouched",
    (text) => {
      expect(run(decimalString, text)).toBe(text);
    },
  );

  it.each([
    ["a JSON number", 1.53],
    ["an integer number", 1],
    ["exponent notation", "1E-7"],
    ["a padded string", " 1.53"],
    ["a trailing-space string", "1.53 "],
    ["a leading plus", "+1.5"],
    ["a bare dot", ".5"],
    ["a trailing dot", "5."],
    ["NaN text", "NaN"],
    ["Infinity text", "Infinity"],
    ["an empty string", ""],
    ["a grouped number", "1,000.00"],
    ["null", null],
  ])("rejects %s", (_name, value) => {
    expect(() => run(decimalString, value)).toThrow(ContractError);
  });

  it("keeps digits a float could not: no digit is lost", () => {
    const long = "0.1234567890123456789012345678901234567890";
    expect(run(decimalString, long)).toBe(long);
    expect(Number(long).toString()).not.toBe(long); // proves a float WOULD have changed it
  });
});

describe("dates and instants", () => {
  it.each(["2024-02-29", "2000-02-29", "1999-12-31"])("accepts the date %s", (text) => {
    expect(run(isoDate, text)).toBe(text);
  });

  it.each([
    ["an impossible day", "2024-02-30"],
    ["a non-leap Feb 29", "2023-02-29"],
    ["a century non-leap Feb 29", "1900-02-29"],
    ["month 13", "2024-13-01"],
    ["month 00", "2024-00-10"],
    ["a timestamp", "2024-05-03T00:00:00Z"],
    ["a slash date", "2024/05/03"],
    ["a number", 20240503],
  ])("rejects %s as a date", (_name, value) => {
    expect(() => run(isoDate, value)).toThrow(ContractError);
  });

  it.each(["2024-03-15T21:30:15Z", "2024-03-15T11:00:00.5Z", "2024-03-15T11:00:00+00:00", "2024-03-15T07:00:00-04:00"])(
    "accepts the instant %s",
    (text) => {
      expect(run(isoInstant, text)).toBe(text);
    },
  );

  it.each([
    "2024-03-15T25:00:00Z",
    "2024-03-15T10:61:00Z",
    "2024-03-15T10:00:61Z",
    "2024-03-15T25:61:00+99:99",
    "2024-03-15T10:00:00+24:00",
    "2024-03-15T10:00:00+05:60",
  ])("rejects the out-of-range clock or offset %s (a decoded instant must be displayable)", (text) => {
    expect(() => run(isoInstant, text)).toThrow(ContractError);
  });

  it.each(["2024-03-15", "2024-03-15 21:30:15Z", "2024-03-15T21:30:15", "2024-02-30T10:00:00Z", 1710000000])(
    "rejects %j as an instant",
    (value) => {
      expect(() => run(isoInstant, value)).toThrow(ContractError);
    },
  );
});

describe("closed lists, identifiers and primitives", () => {
  it("rejects an unknown enum member instead of mapping it to a generic value", () => {
    const status = oneOf(["A", "B"] as const, "status");
    expect(run(status, "A")).toBe("A");
    expect(() => run(status, "C")).toThrow(ContractError);
    expect(() => run(status, "a")).toThrow(ContractError);
    expect(() => run(status, 1)).toThrow(ContractError);
  });

  it("validates UUIDs and patterns", () => {
    expect(run(uuid, "11111111-1111-4111-8111-111111111111")).toBe("11111111-1111-4111-8111-111111111111");
    expect(() => run(uuid, "not-a-uuid")).toThrow(ContractError);
    expect(() => run(uuid, "11111111111141118111111111111111")).toThrow(ContractError);
    expect(run(matching(/^\d{3}$/, "three digits"), "123")).toBe("123");
    expect(() => run(matching(/^\d{3}$/, "three digits"), "1234")).toThrow(ContractError);
  });

  it("bounds integers and rejects floats, strings and unsafe values", () => {
    expect(run(integer(1, 200), 50)).toBe(50);
    for (const bad of [0, 201, 1.5, "5", Number.NaN, Number.MAX_SAFE_INTEGER + 2]) {
      expect(() => run(integer(1, 200), bad)).toThrow(ContractError);
    }
    expect(() => run(nonNegativeInteger, -1)).toThrow(ContractError);
  });

  it("literal accepts only the exact constant", () => {
    expect(run(literal(true), true)).toBe(true);
    expect(() => run(literal(true), false)).toThrow(ContractError);
    expect(() => run(literal(true), "true")).toThrow(ContractError);
    expect(run(literal("ratio"), "ratio")).toBe("ratio");
  });

  it("distinguishes strings, non-empty strings and booleans", () => {
    expect(run(string, "")).toBe("");
    expect(() => run(nonEmptyString, "")).toThrow(ContractError);
    expect(() => run(nonEmptyString, "   ")).toThrow(ContractError);
    expect(() => run(string, 1)).toThrow(ContractError);
    expect(run(boolean, false)).toBe(false);
    expect(() => run(boolean, 0)).toThrow(ContractError);
  });
});

describe("arrays, records and refinements", () => {
  it("reports the index of the failing element", () => {
    expect(decode(arrayOf(string), ["a", 2])).toMatchObject({ ok: false, path: "$[1]" });
    expect(() => run(arrayOf(string), "a")).toThrow(ContractError);
  });

  it("keeps a __proto__ key as data: it never changes the prototype", () => {
    const decoded = run(recordOf(string), JSON.parse('{"__proto__":"polluted","x":"y"}'));
    expect(Object.getPrototypeOf(decoded)).toBe(Object.prototype);
    expect(Object.keys(decoded).sort()).toEqual(["__proto__", "x"]);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it("decodes a string map and reports the failing key", () => {
    expect(run(recordOf(string), { database: "OperationalError" })).toEqual({ database: "OperationalError" });
    expect(decode(recordOf(string), { database: 1 })).toMatchObject({ ok: false, path: "$.database" });
  });

  it("applies a cross-field rule after the shape is valid", () => {
    const pair = refine(object<{ lo: number; hi: number }>({ lo: integer(), hi: integer() }), (v, path) => {
      if (v.lo > v.hi) reject(path, "lo not above hi");
    });
    expect(run(pair, { lo: 1, hi: 2 })).toEqual({ lo: 1, hi: 2 });
    expect(() => run(pair, { lo: 3, hi: 2 })).toThrow(ContractError);
  });
});

describe("failures never echo the received value", () => {
  it("names the path and the expectation only", () => {
    const outcome = decode(object<{ secret: string }>({ secret: string }), { secret: 12345678 });
    expect(outcome).toEqual({ ok: false, path: "$.secret", expected: "a string" });
    expect(JSON.stringify(outcome)).not.toContain("12345678");
  });

  it("does not swallow errors that are not contract failures", () => {
    const boom = () => {
      throw new TypeError("a bug");
    };
    expect(() => decode(boom, 1)).toThrow(TypeError);
  });
});
