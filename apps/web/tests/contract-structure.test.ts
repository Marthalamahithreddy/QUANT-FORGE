// @vitest-environment node
/**
 * Structural guards for the 11.1C architecture. They read the source tree, so a future edit that
 * quietly weakens a boundary fails here rather than in review.
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../src");
const ROOT = resolve(__dirname, "..");

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}
const files = walk(SRC).filter((f) => !f.includes("/types/generated/"));
const read = (path: string) => readFileSync(path, "utf8");
const code = (source: string) =>
  source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
const rel = (file: string) => relative(SRC, file);

/**
 * Numeric coercion of a (possibly financial) value: Number(), parseFloat/parseInt, toFixed, Math.*,
 * BigDecimal-style helpers, and unary plus. Integers for paging live outside the scanned layers.
 */
const COERCION: [string, RegExp][] = [
  ["Number(...)", /\bNumber\(/],
  ["Number.parseFloat / Number.parseInt", /Number\.parse(Float|Int)/],
  ["parseFloat(...)", /\bparseFloat\(/],
  ["parseInt(...)", /\bparseInt\(/],
  [".toFixed(...)", /\.toFixed\(/],
  ["Math.*", /\bMath\./],
  ["unary plus coercion", /(^|[=(,:?&|!\[])\s*\+\s*[A-Za-z_(]/m],
];

describe("the coercion guard itself", () => {
  it.each([
    ["Number(...)", "const n = Number(value);"],
    ["Number.parseFloat / Number.parseInt", "Number.parseFloat(x)"],
    ["parseFloat(...)", "return parseFloat(x);"],
    ["parseInt(...)", "parseInt(x, 10)"],
    [".toFixed(...)", "n.toFixed(2)"],
    ["Math.*", "Math.round(n)"],
    ["unary plus coercion", "const n = +text;"],
  ])("detects %s", (name, snippet) => {
    const pattern = COERCION.find(([label]) => label === name)![1];
    expect(snippet).toMatch(pattern);
  });

  it("leaves ordinary code alone", () => {
    for (const [, pattern] of COERCION) expect("const a = b + c; x = `${y}`; Number.isSafeInteger(z);").not.toMatch(pattern);
  });
});

describe("the decimal boundary: no financial value becomes a JS number in the contract layer", () => {
  const LAYER = [
    "lib/api/decode.ts",
    "lib/api/decoders/earnings.ts",
    "lib/api/decoders/filings.ts",
    "lib/api/decoders/health.ts",
    "lib/api/schema.ts",
    "lib/api/client.ts",
    "lib/api/earnings.ts",
    "lib/api/filings.ts",
    "lib/api/health.ts",
    "lib/api/echo.ts",
    "lib/api/result.ts",
    "lib/api/messages.ts",
    "components/financial/financial-value.tsx",
    "components/financial/percent-value.tsx",
    "components/earnings/earnings-table.tsx",
    "components/earnings/event-detail.tsx",
    "components/filings/eps-panels.tsx",
    "components/filings/filings-table.tsx",
  ];

  it.each(LAYER)("%s contains no numeric coercion", (file) => {
    const source = code(read(join(SRC, file)));
    for (const [name, pattern] of COERCION) {
      expect(source, `${file}: ${name}`).not.toMatch(pattern);
    }
  });

  it("every file listed exists (the guard cannot silently go stale)", () => {
    for (const file of LAYER) expect(existsSync(join(SRC, file)), file).toBe(true);
  });

  it("date arithmetic is isolated in one module that no financial component imports", () => {
    const importers = files.filter((f) => /from "@\/lib\/calendar"/.test(read(f))).map(rel).sort();
    expect(importers).toEqual(["lib/api/decode.ts", "lib/earnings-view.ts"]);
  });

  it("the formatting module converts only through BigInt on strings", () => {
    const source = code(read(join(SRC, "lib/format.ts")));
    expect(source).not.toMatch(/\bNumber\(|parseFloat|toFixed|\+\s*raw\b/);
  });
});

describe("the server boundary: the browser never reaches the API", () => {
  const importsOf = (file: string): string[] =>
    [...read(file).matchAll(/from\s+["']([^"']+)["']/g)].map((m) => m[1]!).flatMap((spec) => {
      if (spec.startsWith("@/")) return [resolveModule(join(SRC, spec.slice(2)))];
      if (spec.startsWith(".")) return [resolveModule(resolve(dirname(file), spec))];
      return [];
    });
  function resolveModule(base: string): string {
    for (const candidate of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]) {
      if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
    }
    return base;
  }
  const isClient = (file: string) => /^["']use client["']/m.test(read(file));
  const clients = files.filter(isClient);

  /** Every module a client component can pull into the browser bundle. */
  function reachable(entry: string): Set<string> {
    const seen = new Set<string>();
    const queue = [entry];
    while (queue.length > 0) {
      const next = queue.pop()!;
      if (seen.has(next) || !existsSync(next)) continue;
      seen.add(next);
      queue.push(...importsOf(next));
    }
    return seen;
  }

  it("has client components to check", () => {
    expect(clients.map(rel).sort()).toEqual([
      "app/error.tsx",
      "components/layout/header-context.tsx",
      "components/layout/mobile-nav.tsx",
      "components/layout/nav-link.tsx",
      "components/layout/theme-control.tsx",
      "components/ui/evidence-drawer.tsx",
    ]);
  });

  it.each(clients.map(rel))("%s cannot reach any API, config or decoder module", (name) => {
    const graph = [...reachable(join(SRC, name))].map(rel);
    const leaks = graph.filter((f) => f.startsWith("lib/api/") || f.startsWith("lib/config/") || f === "lib/earnings-view.ts");
    expect(leaks).toEqual([]);
  });

  it("only modules under lib/api import the HTTP client", () => {
    const importers = files.filter((f) => /@\/lib\/api\/client/.test(read(f))).map(rel).sort();
    expect(importers).toEqual(["lib/api/earnings.ts", "lib/api/filings.ts", "lib/api/health.ts", "lib/api/research.ts"]);
  });

  it("fetch is called in exactly one place", () => {
    const callers = files.filter((f) => /\bfetch\(/.test(code(read(f)))).map(rel);
    expect(callers).toEqual(["lib/api/client.ts"]);
  });

  it("the API base URL is read only through the config module", () => {
    const readers = files.filter((f) => /process\.env/.test(code(read(f)))).map(rel).sort();
    // config/env.ts owns the variable; messages.ts reads only NODE_ENV to decide dev-only wording.
    expect(readers).toEqual(["lib/api/messages.ts", "lib/config/env.ts"]);
    expect(code(read(join(SRC, "lib/api/messages.ts")))).not.toMatch(/NEXT_PUBLIC|API_BASE/);
  });

  it("makes no mutating request: every API call is a GET", () => {
    expect(code(read(join(SRC, "lib/api/client.ts")))).toMatch(/method: "GET"/);
    for (const file of files) expect(code(read(file)), rel(file)).not.toMatch(/method:\s*["'](POST|PUT|PATCH|DELETE)/i);
  });
});

describe("the shell lives in the layout, once", () => {
  const importsShell = (file: string) => /@\/components\/layout\/app-shell/.test(read(file));

  it("only the root layout renders the app shell", () => {
    expect(files.filter(importsShell).map(rel).sort()).toEqual(["app/layout.tsx"]);
  });

  it("no page or loading file instantiates its own shell, header or status chip", () => {
    for (const file of files.filter((f) => /app\/.*(page|loading|error|not-found)\.tsx$/.test(f))) {
      const source = read(file);
      expect(source, rel(file)).not.toMatch(/AppShell|<Header|ApiStatus|getBackendStatus/);
    }
  });

  it("the layout streams the API status behind Suspense and is not a client component", () => {
    const layout = read(join(SRC, "app/layout.tsx"));
    expect(layout).toMatch(/<Suspense fallback=\{<ApiStatus \/>\}>\s*<LiveApiStatus \/>/);
    expect(layout).not.toMatch(/^["']use client["']/m);
  });

  it("no page or component other than the status components reads the shell's health", () => {
    const readers = files.filter((f) => /getRequestBackendStatus|getBackendStatus/.test(read(f))).map(rel).sort();
    expect(readers).toEqual(["app/page.tsx", "components/layout/live-api-status.tsx", "lib/api/health.ts"]);
  });
});

describe("legacy code is retired where the surfaces migrated", () => {
  it("has no Badge, StatePanel, sorting or page-local filtering left", () => {
    for (const file of files) {
      const source = code(read(file));
      expect(source, rel(file)).not.toMatch(/components\/ui\/(badge|state-panel)|\bStatePanel\b|\bsortEvents\b|\bmatchesFilters\b|\bcompareDecimalStrings\b/);
    }
    expect(existsSync(join(SRC, "components/ui/badge.tsx"))).toBe(false);
    expect(existsSync(join(SRC, "components/ui/state-panel.tsx"))).toBe(false);
  });

  it("has no hand-written wire types: the types come from the generated file", () => {
    expect(existsSync(join(SRC, "types/earnings.ts"))).toBe(false);
    expect(existsSync(join(SRC, "types/filings.ts"))).toBe(false);
    expect(existsSync(join(SRC, "types/api.ts"))).toBe(false);
    expect(read(join(SRC, "lib/api/schema.ts"))).toMatch(/from "@\/types\/generated\/api"/);
  });

  it("the generated types never enter runtime code (type-only imports)", () => {
    const offenders = files
      .filter((f) => /@\/types\/generated/.test(read(f)))
      .filter((f) => !/import type \{[^}]*\} from "@\/types\/generated\/api"/.test(read(f)))
      .map(rel);
    expect(offenders).toEqual([]);
  });

  it("the data table has no client-side sorting state", () => {
    const source = code(read(join(SRC, "components/ui/data-table.tsx")));
    expect(source).not.toMatch(/useState|useMemo|onClick|aria-sort|\.sort\(/);
  });

  it("has no whole-row click handler anywhere in the data components", () => {
    for (const file of files.filter((f) => /components\/(ui|earnings|filings)\//.test(f))) {
      expect(code(read(file)), rel(file)).not.toMatch(/<tr[^>]*onClick|role="link"/);
    }
  });
});

describe("tooling and dependencies", () => {
  const pkg = JSON.parse(read(join(ROOT, "package.json"))) as {
    dependencies: Record<string, string>;
    devDependencies: Record<string, string>;
    scripts: Record<string, string>;
  };

  it("adds only openapi-typescript (dev) in 11.1C: no chart, table, state, date or icon library", () => {
    expect(Object.keys(pkg.dependencies).sort()).toEqual(["next", "react", "react-dom"]);
    expect(pkg.devDependencies["openapi-typescript"]).toBeDefined();
    const banned = /chart|recharts|d3|table|tanstack|zustand|redux|swr|query|date-fns|dayjs|moment|lucide|icons|playwright|server-only/i;
    for (const name of [...Object.keys(pkg.dependencies), ...Object.keys(pkg.devDependencies)]) {
      expect(name, name).not.toMatch(banned);
    }
  });

  it("exposes the contract commands", () => {
    expect(pkg.scripts["contract:export"]).toBe("node scripts/openapi.mjs export");
    expect(pkg.scripts["contract:check"]).toBe("node scripts/openapi.mjs check");
  });
});
