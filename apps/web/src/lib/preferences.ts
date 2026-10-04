/**
 * Server-readable UI preferences (design spec section 7.2): small cookies, never localStorage.
 *
 * The server reads them in the root layout and renders the matching attributes, so the first
 * paint is already correct (no flash). Only presentation preferences live here: a benchmark,
 * filing or EPS selection is NEVER a preference.
 */

export const THEME_COOKIE = "qf-theme";
export const TINT_COOKIE = "qf-tint";

export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

/** One year, in seconds. */
export const PREFERENCE_MAX_AGE = 60 * 60 * 24 * 365;

/** An unknown or absent cookie value is `system`: the operating-system preference decides. */
export function parseTheme(raw: string | undefined): Theme {
  return (THEMES as readonly string[]).includes(raw ?? "") ? (raw as Theme) : "system";
}

/** Direction tint is OFF unless the cookie is exactly `on`. */
export function parseTint(raw: string | undefined): boolean {
  return raw === "on";
}
