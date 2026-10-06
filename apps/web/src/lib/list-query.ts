/**
 * URL state of the list pages (spec sections 7.3 and Appendix D).
 *
 * Parsing is strict and local: a repeated parameter, a malformed value or an out-of-range page is
 * reported BEFORE any request, and nothing is coerced or repaired. Defaults (an empty filter,
 * page 1) are omitted from the canonical URL; a request for a non-canonical form of a valid query
 * is redirected to the canonical one, so every list view has exactly one address.
 */

import { PAGE_SIZE } from "@/lib/api/schema";

export type RawSearchParams = Record<string, string | string[] | undefined>;

/** Highest page number the URL accepts (Appendix D): offset stays inside the API's bound. */
export const MAX_PAGE = 10_000;

export interface ListError {
  /** The URL parameter at fault. */
  readonly param: string;
  readonly message: string;
}

export interface RawParam {
  /** The single value, untouched ("" when absent). */
  readonly text: string;
  readonly repeated: boolean;
  readonly present: boolean;
}

export function readParam(raw: RawSearchParams, key: string): RawParam {
  const value = raw[key];
  if (Array.isArray(value)) return { text: value[0] ?? "", repeated: value.length > 1, present: true };
  return { text: value ?? "", repeated: false, present: value !== undefined };
}

const PAGE_NUMBER = /^[1-9]\d{0,4}$/;

/** The 1-based page; page 1 when the parameter is absent. Anything else out of range is an error. */
export function parsePage(raw: RawSearchParams): { page: number; error: ListError | null } {
  const param = readParam(raw, "page");
  const message = `Page must be a whole number from 1 to ${MAX_PAGE}, as a single value.`;
  // Absent, and present-but-empty (`page=`), both mean the default: the latter is redirected away.
  if (!param.present || (param.text === "" && !param.repeated)) return { page: 1, error: null };
  if (param.repeated || !PAGE_NUMBER.test(param.text) || Number(param.text) > MAX_PAGE) {
    return { page: 1, error: { param: "page", message } };
  }
  return { page: Number(param.text), error: null };
}

export const offsetOfPage = (page: number): number => (page - 1) * PAGE_SIZE;


/** A canonical path: `path` plus the given parameters in a fixed order, omitting empty ones. */
export function canonicalHref(path: string, entries: readonly (readonly [string, string | undefined])[], page: number): string {
  const params = new URLSearchParams();
  for (const [key, value] of entries) {
    if (value) params.set(key, value);
  }
  if (page > 1) params.set("page", String(page));
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

/**
 * Whether the request URL already IS the canonical one: the same parameters with the same
 * values, no extra or empty ones. (Order does not matter.)
 */
export function isCanonical(raw: RawSearchParams, href: string): boolean {
  const canonical = new URLSearchParams(href.split("?")[1] ?? "");
  const keys = Object.keys(raw).filter((key) => raw[key] !== undefined);
  if (keys.length !== [...canonical.keys()].length) return false;
  return keys.every((key) => raw[key] === canonical.get(key));
}
