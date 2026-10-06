import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The token file is the tested artefact: jsdom cannot measure colour, so contrast is recomputed
 * from the literal values in `globals.css`. This is a guard on the tokens, not a claim of full
 * WCAG conformance (focus order, target size, zoom and screen-reader behaviour are reviewed
 * separately).
 */

const CSS = readFileSync(resolve(__dirname, "../src/app/globals.css"), "utf8");

/** The declarations of the first rule whose selector (whitespace-normalised) equals `selector`. */
function block(selector: string): Record<string, string> {
  const open = new RegExp(`(?:^|\\n)\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{`, "m").exec(CSS);
  if (!open) throw new Error(`no rule for ${selector}`);
  let depth = 1;
  let i = open.index + open[0].length;
  const start = i;
  while (depth > 0 && i < CSS.length) {
    const ch = CSS[i++];
    if (ch === "{") depth++;
    if (ch === "}") depth--;
  }
  const body = CSS.slice(start, i - 1);
  const vars: Record<string, string> = {};
  for (const m of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) vars[m[1] as string] = (m[2] as string).trim();
  return vars;
}

const DARK = { ...block(":root,\n:root[data-theme=\"dark\"]") };
const LIGHT = block(':root[data-theme="light"]');
const LIGHT_MEDIA = block(':root:not([data-theme="dark"])');

function resolveVar(theme: Record<string, string>, name: string): string {
  const value = theme[name];
  if (value === undefined) throw new Error(`missing ${name}`);
  const ref = /^var\((--[a-z0-9-]+)\)$/.exec(value);
  return ref ? resolveVar(theme, ref[1] as string) : value;
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = Number.parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const TOKENS = [
  "--bg",
  "--surface",
  "--surface-elevated",
  "--surface-raised",
  "--border-subtle",
  "--border",
  "--border-control",
  "--text",
  "--text-2",
  "--text-3",
  "--accent",
  "--accent-subtle",
  "--accent-contrast",
  "--info",
  "--warning",
  "--danger",
  "--success",
  "--mode-descriptive",
  "--mode-pit",
  "--dir-positive",
  "--dir-negative",
  "--chart-stock",
  "--chart-bench",
  "--chart-abnormal",
  "--chart-grid",
  "--chart-zero",
  "--hover",
  "--selected",
  "--focus",
];

describe("token presence", () => {
  it.each(["dark", "light"] as const)("defines every semantic token for %s", (name) => {
    const theme = name === "dark" ? DARK : LIGHT;
    for (const token of TOKENS) expect(theme[token], `${name} ${token}`).toBeDefined();
  });

  it("keeps the system-light (media) block identical to the explicit light theme", () => {
    expect(LIGHT_MEDIA).toEqual(LIGHT);
  });

  it("keeps the pre-V2 token names as compatibility aliases", () => {
    for (const alias of [
      "--color-surface-0",
      "--color-surface-1",
      "--color-surface-2",
      "--color-surface-3",
      "--color-border-strong",
      "--color-border-subtle",
      "--color-text-primary",
      "--color-text-secondary",
      "--color-text-muted",
      "--color-status-ok",
      "--color-status-warn",
      "--color-status-down",
    ]) {
      expect(CSS).toContain(alias);
    }
  });

  it("defines the three approved container widths", () => {
    expect(CSS).toMatch(/--container-reading:\s*64rem/);
    expect(CSS).toMatch(/--container-workspace:\s*96rem/);
    expect(CSS).toMatch(/--container-table:\s*120rem/);
  });

  it("does not apply tabular numerals globally and keeps them to `.num`", () => {
    const body = /body\s*\{[^}]*\}/.exec(CSS)?.[0] ?? "";
    expect(body).not.toMatch(/tnum|font-feature-settings|font-variant-numeric/);
    expect(CSS).toMatch(/\.num,\s*\n\s*\.font-tabular\s*\{\s*font-variant-numeric:\s*tabular-nums/);
  });

  it("uses a system font stack and no remote font", () => {
    expect(CSS).not.toMatch(/@import\s+url|fonts\.googleapis|@font-face/);
  });
});

describe.each([
  ["dark", DARK],
  ["light", LIGHT],
] as const)("contrast (%s)", (_name, theme) => {
  const c = (fg: string, bg: string) => contrast(resolveVar(theme, fg), resolveVar(theme, bg));
  const surfaces = ["--bg", "--surface", "--surface-raised"];

  it.each(["--text", "--text-2", "--text-3"])("normal text %s reaches 4.5:1 on every surface", (fg) => {
    for (const bg of surfaces) expect(c(fg, bg), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
  });

  it("links and the accent reach 4.5:1 on surfaces", () => {
    for (const bg of surfaces) expect(c("--accent", bg)).toBeGreaterThanOrEqual(4.5);
  });

  it("control borders reach 3:1 on every surface a control sits on (WCAG 1.4.11)", () => {
    for (const bg of ["--bg", "--surface", "--surface-elevated"]) {
      expect(c("--border-control", bg), `border-control on ${bg}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("the decorative hairline is NOT relied on as a control boundary (it is below 3:1)", () => {
    expect(c("--border", "--surface-elevated")).toBeLessThan(3);
  });

  it("the focus indicator reaches 3:1 against the page and surface", () => {
    for (const bg of ["--bg", "--surface"]) expect(c("--focus", bg)).toBeGreaterThanOrEqual(3);
  });

  it.each(["--info", "--warning", "--danger", "--success", "--mode-pit"])(
    "status text %s reaches 4.5:1 on surfaces",
    (fg) => {
      for (const bg of surfaces) expect(c(fg, bg), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(["--dir-positive", "--dir-negative"])("direction mark %s reaches 3:1 on surfaces", (fg) => {
    for (const bg of surfaces) expect(c(fg, bg), `${fg} on ${bg}`).toBeGreaterThanOrEqual(3);
  });

  it.each(["--chart-stock", "--chart-bench", "--chart-abnormal", "--chart-zero"])("chart mark/line %s reaches 3:1 on surfaces (WCAG 1.4.11)", (fg) => {
    for (const bg of ["--bg", "--surface"]) expect(c(fg, bg), `${fg} on ${bg}`).toBeGreaterThanOrEqual(3);
  });

  it("the three chart series are three different colours (and shape and fill differ too: see the chart tests)", () => {
    const series = ["--chart-stock", "--chart-bench", "--chart-abnormal"].map((token) => resolveVar(theme, token));
    expect(new Set(series).size).toBe(3);
  });

  it("the point-in-time and descriptive hues differ and both read on surfaces", () => {
    expect(resolveVar(theme, "--mode-pit")).not.toBe(resolveVar(theme, "--mode-descriptive"));
    for (const bg of surfaces) expect(c("--mode-descriptive", bg)).toBeGreaterThanOrEqual(4.5);
  });

  it("text on a solid accent button reaches 4.5:1", () => {
    expect(c("--accent-contrast", "--accent")).toBeGreaterThanOrEqual(4.5);
  });

  it("the selected fill keeps primary text readable", () => {
    expect(c("--text", "--selected")).toBeGreaterThanOrEqual(4.5);
  });
});
