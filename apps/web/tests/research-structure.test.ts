// @vitest-environment node
/**
 * Structural guards for the research workspace: the finance-arithmetic boundary, the chart
 * geometry boundary, no automatic benchmark or filing, no trading language and the server bias.
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../src");
const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
const files = walk(SRC).filter((f) => !f.includes("/types/generated/"));
const read = (path: string) => readFileSync(path, "utf8");
const code = (source: string) =>
  source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
const rel = (file: string) => relative(SRC, file);

const researchFiles = [
  ...files.filter((f) => rel(f).startsWith("components/research/")),
  ...["lib/research-rows.ts", "lib/research-wording.ts", "lib/api/decoders/research.ts", "lib/api/research.ts", "components/financial/eps-surprise.tsx"].map((f) => join(SRC, f)),
];
const COERCION = [/\bNumber\(/, /Number\.parse(Float|Int)/, /\bparseFloat\(/, /\bparseInt\(/, /\.toFixed\(/, /\bMath\./, /(^|[=(,:?&|!\[])\s*\+\s*[A-Za-z_(]/m];

describe("no finance arithmetic in React: research reads exact strings and shows the backend's figures", () => {
  it("lists the research files it guards (so the guard cannot go stale)", () => {
    expect(researchFiles.length).toBeGreaterThan(15);
    for (const file of researchFiles) expect(existsSync(file), file).toBe(true);
  });

  it.each(researchFiles.map(rel))("%s has no numeric coercion", (name) => {
    const source = code(read(join(SRC, name)));
    for (const pattern of COERCION) expect(source, `${name}: ${pattern}`).not.toMatch(pattern);
  });

  it("never subtracts or combines return figures: the abnormal return is only ever read from the response", () => {
    for (const file of researchFiles) {
      const source = code(read(file));
      expect(source, rel(file)).not.toMatch(/(return_value|benchmark_return|abnormal_return)\s*[-+*/]\s*[A-Za-z(]/);
      expect(source, rel(file)).not.toMatch(/[A-Za-z)]\s*[-+*/]\s*(return_value|benchmark_return|abnormal_return)\b/);
    }
    const table = code(read(join(SRC, "components/research/returns-table.tsx")));
    expect(table).toMatch(/abnormal\.abnormal_return/);
  });

  it("the query layer converts only integers (horizons, pages), never a financial value", () => {
    const source = code(read(join(SRC, "lib/research-view.ts")));
    const uses = source.match(/Number\.parseInt\([^)]*\)/g) ?? [];
    expect(uses.length).toBeGreaterThan(0);
    expect(source).not.toMatch(/\bNumber\(|parseFloat|toFixed|Math\./);
  });
});

describe("chart geometry boundary", () => {
  const geometry = join(SRC, "lib/chart/geometry.ts");
  const chartFiles = files.filter((f) => rel(f).startsWith("lib/chart/"));

  it("geometry.ts is the only chart module, and the only research-related file that converts a decimal string to a number", () => {
    expect(chartFiles.map(rel)).toEqual(["lib/chart/geometry.ts"]);
    const converters = files
      .filter((f) => /\bNumber\(/.test(code(read(f))))
      .map(rel)
      .sort();
    // calendar validity (dates), whole page numbers in the list URLs, and the chart geometry:
    // nothing else in the app uses Number(...), and no financial value reaches the first two.
    expect(converters).toEqual(["lib/calendar.ts", "lib/chart/geometry.ts", "lib/list-query.ts"]);
  });

  it("only the chart component imports the geometry module, and geometry imports nothing from the app", () => {
    const importers = files.filter((f) => /@\/lib\/chart\/geometry/.test(read(f))).map(rel).sort();
    expect(importers).toEqual(["components/research/horizon-returns-chart.tsx"]);
    expect(read(geometry)).not.toMatch(/from "@\//);
  });

  it("geometry feeds neither a financial label, a refusal decision nor a point-in-time decision", () => {
    const geometryExports = [...read(geometry).matchAll(/export (?:function|const) (\w+)/g)].map((m) => m[1]!);
    expect(geometryExports.sort()).toEqual(["axisScale", "markPosition", "slotCentre", "tickIndexes", "tickLabel", "tickPosition"]);
    for (const file of files.filter((f) => !rel(f).startsWith("lib/chart/") && rel(f) !== "components/research/horizon-returns-chart.tsx")) {
      for (const name of geometryExports) expect(code(read(file)), `${rel(file)} uses ${name}`).not.toMatch(new RegExp(`\\b${name}\\(`));
    }
    const chart = code(read(join(SRC, "components/research/horizon-returns-chart.tsx")));
    // coordinates are used for placement only: never inside text, labels or aria strings
    expect(chart).not.toMatch(/(aria-label|<text[^>]*>)\s*\{?[^}]*\b(stockY|benchY|zeroY|x)\b\s*\}/);
    // data labels come from the exact strings
    expect(chart).toMatch(/formatRatioAsPercent\(/);
  });

  it("tick labels are built from integers: no float formatting in the geometry module", () => {
    const source = code(read(geometry));
    expect(source).not.toMatch(/toFixed|toPrecision|toLocaleString|Intl\./);
  });
});

describe("no automatic benchmark or filing, no recommendation", () => {
  const research = [...files.filter((f) => /research/i.test(rel(f))), join(SRC, "components/earnings/event-detail.tsx")];

  it("hardcodes no benchmark id, symbol or default", () => {
    for (const file of files) {
      const source = code(read(file));
      expect(source, rel(file)).not.toMatch(/\bSPY\b|\bQQQ\b|S&P ?500|defaultBenchmark|recommendedBenchmark|autoBenchmark/i);
      expect(source, rel(file)).not.toMatch(/benchmark_id=[0-9a-f]{8}-/i);
    }
  });

  it("never infers a filing: no nearest, latest, same-day or CIK-matched accession is ever chosen", () => {
    for (const file of research) {
      const source = code(read(file));
      expect(source, rel(file)).not.toMatch(/nearestFiling|latestFiling|sameDayFiling|autoLink|inferFiling|matchFiling|pickFiling/i);
      expect(source, rel(file)).not.toMatch(/\/api\/v1\/filings|getFilings\(/); // the workspace never lists filings
    }
  });

  it("the workspace sends the observation request only from parsed URL state", () => {
    const results = code(read(join(SRC, "components/research/research-results.tsx")));
    expect(results.match(/getObservation\(/g)).toHaveLength(1);
    expect(results).toMatch(/getObservation\(eventId, config\.query\)/);
    const preview = code(read(join(SRC, "components/research/market-reaction-preview.tsx")));
    expect(preview).toMatch(/getObservation\(eventId, \{\}\)/);
  });

  it("uses no trading, scoring or predictive vocabulary in code or copy", () => {
    const WORDS = /\b(buy|sell|bullish|bearish|outperform|underperform|confidence|ranking|predictive|price target|trading signal)\b/i;
    for (const file of research) {
      const source = code(read(file))
        .replace(/Not a signal, forecast or recommendation\./g, "")
        .replace(/not a recommendation/g, "");
      expect(source, rel(file)).not.toMatch(WORDS);
    }
  });

  it("avoids availability language the evidence does not support", () => {
    const wording = code(read(join(SRC, "lib/research-wording.ts")));
    const withoutNegations = wording.replace(/It implies no time, no first public disclosure and no availability[^"]*"/g, '"');
    expect(withoutNegations).not.toMatch(/tradable|safe to trade|known to the market|first public disclosure|made public|released at/i);
    expect(wording).not.toMatch(/\bverified\b/i);
  });
});

describe("server bias and dependencies", () => {
  it("the only client components are the existing ones plus the evidence drawer", () => {
    const clients = files.filter((f) => /^["']use client["']/m.test(read(f))).map(rel).sort();
    expect(clients).toEqual([
      "app/error.tsx",
      "components/layout/header-context.tsx",
      "components/layout/mobile-nav.tsx",
      "components/layout/nav-link.tsx",
      "components/layout/theme-control.tsx",
      "components/ui/evidence-drawer.tsx",
    ]);
  });

  it("the chart, tables, ledger and workspace are server components with no store and no effect", () => {
    for (const file of files.filter((f) => rel(f).startsWith("components/research/"))) {
      expect(read(file), rel(file)).not.toMatch(/use client|useState|useEffect|useReducer|createContext|zustand|localStorage/);
    }
  });

  it("adds no chart, table, state, date or icon library", () => {
    const pkg = JSON.parse(read(join(SRC, "../package.json"))) as { dependencies: Record<string, string>; devDependencies: Record<string, string> };
    expect(Object.keys(pkg.dependencies).sort()).toEqual(["next", "react", "react-dom"]);
    const dev = Object.keys(pkg.devDependencies).filter((name) => /chart|d3|plot|recharts|table|date|moment|icon|lucide|zustand|redux/i.test(name));
    expect(dev).toEqual([]);
  });

  it("the observation uses the long timeout class and nothing else does", () => {
    const users = files.filter((f) => /OBSERVATION_TIMEOUT_MS/.test(read(f))).map(rel).sort();
    expect(users).toEqual(["lib/api/client.ts", "lib/api/research.ts"]);
  });

  it("makes no write, save or verification request", () => {
    for (const file of researchFiles) {
      const source = code(read(file));
      expect(source, rel(file)).not.toMatch(/method:\s*["'](POST|PUT|PATCH|DELETE)|\/verify|research\/records/i);
    }
  });
});
