/**
 * Strict runtime decoders: the only way a network payload becomes a typed value.
 *
 * A decoder either returns a NEW value built from the documented keys only, or throws
 * `ContractError`. Rules that every decoder here enforces:
 *
 * - every documented key must be PRESENT (`null` is a value; absent is contract drift);
 * - decimals are strings matching `-?\d+(\.\d+)?` exactly (a JSON number, padding or an exponent
 *   is rejected, never repaired) and are never converted to a JS number;
 * - an enum member the decoder does not know is rejected, never rendered as a generic "unknown";
 * - extra keys are ignored (additive backend changes must not break the UI) and are not copied.
 *
 * `ContractError` carries the field PATH and what was expected, never the received value, so a
 * contract failure can be shown without echoing the payload.
 */

import { isCalendarDate, isValidClock } from "@/lib/calendar";

export class ContractError extends Error {
  readonly path: string;
  readonly expected: string;

  constructor(path: string, expected: string) {
    super(`${path}: expected ${expected}`);
    this.name = "ContractError";
    this.path = path;
    this.expected = expected;
  }
}

/** Reads `value` at `path`, returning the typed result or throwing `ContractError`. */
export type Decoder<T> = (value: unknown, path: string) => T;

const OPTIONAL = Symbol("optional");
type OptionalDecoder<T> = Decoder<T | undefined> & { readonly [OPTIONAL]: true };

const fail = (path: string, expected: string): never => {
  throw new ContractError(path, expected);
};

/** The result of `decode`: a value, or where the contract broke. */
export type DecodeOutcome<T> =
  | { readonly ok: true; readonly data: T }
  | { readonly ok: false; readonly path: string; readonly expected: string };

/** Runs a decoder without throwing. Non-contract exceptions are not swallowed. */
export function decode<T>(decoder: Decoder<T>, value: unknown, path = "$"): DecodeOutcome<T> {
  try {
    return { ok: true, data: decoder(value, path) };
  } catch (error) {
    if (error instanceof ContractError) {
      return { ok: false, path: error.path, expected: error.expected };
    }
    throw error;
  }
}

// ── primitives ───────────────────────────────────────────────────────────────────────────

export const string: Decoder<string> = (value, path) =>
  typeof value === "string" ? value : fail(path, "a string");

/** A string with visible content: empty and whitespace-only strings are rejected. */
export const nonEmptyString: Decoder<string> = (value, path) =>
  typeof value === "string" && value.trim() !== "" ? value : fail(path, "a non-empty string");

export const boolean: Decoder<boolean> = (value, path) =>
  typeof value === "boolean" ? value : fail(path, "a boolean");

/** A whole number within `[min, max]`. Counts and pagination only, never a financial value. */
export function integer(min = Number.MIN_SAFE_INTEGER, max = Number.MAX_SAFE_INTEGER): Decoder<number> {
  return (value, path) =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max
      ? value
      : fail(path, `an integer from ${min} to ${max}`);
}

export const nonNegativeInteger: Decoder<number> = integer(0);

export function literal<const T extends string | number | boolean>(expected: T): Decoder<T> {
  return (value, path) => (value === expected ? expected : fail(path, `the value ${String(expected)}`));
}

/** One of a closed list. An unknown member is a contract failure by design. */
export function oneOf<const L extends readonly string[]>(list: L, name: string): Decoder<L[number]> {
  return (value, path) =>
    typeof value === "string" && (list as readonly string[]).includes(value)
      ? (value as L[number])
      : fail(path, `a known ${name}`);
}

export function matching(pattern: RegExp, name: string): Decoder<string> {
  return (value, path) =>
    typeof value === "string" && pattern.test(value) ? value : fail(path, name);
}

// ── exact financial and temporal shapes ──────────────────────────────────────────────────

/**
 * A fixed-point decimal STRING exactly as the backend serialises it. A JSON number is rejected
 * (it has already lost digits), as is padding or an exponent. The string is returned untouched.
 */
export const decimalString: Decoder<string> = matching(
  /^-?\d+(?:\.\d+)?$/,
  "an exact decimal string (a JSON number, padding or exponent is not accepted)",
);

/** `YYYY-MM-DD` that is a real calendar date. Never parsed through `Date`. */
export const isoDate: Decoder<string> = (value, path) =>
  typeof value === "string" && isCalendarDate(value) ? value : fail(path, "a calendar date (YYYY-MM-DD)");

const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;

/** An ISO-8601 instant with an explicit offset. Never parsed through `Date`. */
export const isoInstant: Decoder<string> = (value, path) =>
  typeof value === "string" && INSTANT.test(value) && isCalendarDate(value.slice(0, 10)) && isValidClock(value.slice(11))
    ? value
    : fail(path, "an ISO-8601 instant with an offset");

export const uuid: Decoder<string> = matching(
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
  "a UUID",
);

// ── composition ──────────────────────────────────────────────────────────────────────────

/** `null` or the inner shape. Absent is NOT null: the surrounding `object` rejects an absent key. */
export function nullable<T>(inner: Decoder<T>): Decoder<T | null> {
  return (value, path) => (value === null ? null : inner(value, path));
}

export function arrayOf<T>(inner: Decoder<T>): Decoder<T[]> {
  return (value, path) => {
    if (!Array.isArray(value)) return fail(path, "an array");
    return value.map((item, index) => inner(item, `${path}[${index}]`));
  };
}

/** A string-to-`inner` map with arbitrary keys (OpenAPI `additionalProperties`). */
export function recordOf<T>(inner: Decoder<T>): Decoder<Record<string, T>> {
  return (value, path) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return fail(path, "an object");
    // fromEntries defines OWN properties, so a `__proto__` key stays data and never a prototype.
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, inner(item, `${path}.${key}`)]));
  };
}

/** A key the contract documents as optional (it may be absent). Use sparingly. */
export function optional<T>(inner: Decoder<T>): OptionalDecoder<T> {
  const decoder: Decoder<T | undefined> = (value, path) => (value === undefined ? undefined : inner(value, path));
  return Object.assign(decoder, { [OPTIONAL]: true as const });
}

type Shape<T> = { [K in keyof T]-?: Decoder<T[K]> };

/**
 * An object with a decoder per documented key. Every key must be present unless declared
 * `optional`; the result is a fresh object holding only the documented keys.
 */
export function object<T extends object>(shape: Shape<T>): Decoder<T> {
  return (value, path) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return fail(path, "an object");
    }
    const source = value as Record<string, unknown>;
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(shape) as (keyof T & string)[]) {
      const decoder = shape[key] as Decoder<unknown> & { [OPTIONAL]?: true };
      if (!(key in source)) {
        if (decoder[OPTIONAL]) continue;
        return fail(`${path}.${key}`, "the key to be present (null is allowed only where documented)");
      }
      result[key] = decoder(source[key], `${path}.${key}`);
    }
    return result as T;
  };
}

/** Adds a cross-field rule that the per-field shapes cannot express. */
export function refine<T>(inner: Decoder<T>, check: (value: T, path: string) => void): Decoder<T> {
  return (value, path) => {
    const decoded = inner(value, path);
    check(decoded, path);
    return decoded;
  };
}

/** For `refine` callbacks: reject with a contract failure at `path`. */
export const reject = fail;
