// @vitest-environment node
/**
 * Structural guards for Saved Research: it is a read-only viewer (no write path of any kind), the
 * frontend never decides a verification status or diffs a value, nothing is fetched from the
 * browser, a stored result never goes through the live endpoint, and exact strings stay strings.
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

const recordFiles = [
  ...files.filter((f) => rel(f).startsWith("components/records/")),
  ...files.filter((f) => rel(f).startsWith("app/research/saved/")),
  ...["lib/api/records.ts", "lib/api/decoders/records.ts", "lib/api/record-types.ts", "lib/record-view.ts", "lib/record-wording.ts", "lib/stored-result.ts", "lib/changed-paths.ts", "components/state/verification-badge.tsx", "components/ui/copy-value.tsx"].map((f) => join(SRC, f)),
];

describe("saved research is a read-only viewer", () => {
  it("lists the files it guards (so the guard cannot go stale)", () => {
    expect(recordFiles.length).toBeGreaterThan(25);
    for (const file of recordFiles) expect(existsSync(file), file).toBe(true);
  });

  it("has no write path: no non-GET method, no body, no mutation call, no form", () => {
    for (const file of recordFiles) {
      const source = code(read(file));
      expect(source, rel(file)).not.toMatch(/method:\s*["'](POST|PUT|PATCH|DELETE)/i);
      if (rel(file).startsWith("lib/api/")) expect(source, rel(file)).not.toMatch(/\bbody:/);
      expect(source, rel(file)).not.toMatch(/<form\b|<input\b|<textarea\b|<select\b|useActionState|"use server"|'use server'|server action/i);
      expect(source, rel(file)).not.toMatch(/\bfetch\(/);
    }
  });

  it("offers no save, rename, delete or ownership wording as UI text", () => {
    for (const file of recordFiles) {
      const source = read(file);
      // Comments may say what the page does NOT do; rendered strings must not offer it.
      const strings = (code(source).match(/(["'`])(?:(?!\1)[^\\\n]|\\.)*\1|>[^<>{}]+</g) ?? []).join(" ");
      expect(strings, rel(file)).not.toMatch(/\b(save baseline|save this|save your|delete record|rename|update baseline|refresh baseline|replace result|edit record|owner|sign in|log in)\b/i);
    }
  });

  it("only the verification uses the long timeout class; the list and the record keep the default read class", () => {
    const source = code(read(join(SRC, "lib/api/records.ts")));
    const uses = source.match(/OBSERVATION_TIMEOUT_MS/g) ?? [];
    expect(uses).toHaveLength(2); // the import and the one use
    const verify = source.slice(source.indexOf("export async function verifyRecord"));
    expect(verify).toMatch(/timeoutMs: OBSERVATION_TIMEOUT_MS/);
    expect(source.slice(0, source.indexOf("export async function verifyRecord"))).not.toMatch(/timeoutMs/);
  });

  it("the record client exposes only reads", () => {
    const source = code(read(join(SRC, "lib/api/records.ts")));
    const exported = [...source.matchAll(/export (?:async )?(?:function|const) (\w+)/g)].map((m) => m[1]);
    expect(exported.sort()).toEqual(["buildRecordListPath", "getRecord", "getRecordPage", "verifyRecord"]);
  });
});

describe("the frontend neither decides a verification status nor compares values", () => {
  it("no file derives a status from fingerprints, paths or levels", () => {
    for (const file of recordFiles) {
      const source = code(read(file));
      expect(source, rel(file)).not.toMatch(/status\s*=\s*["'](MATCH|MANIFEST_CHANGED|RESULT_CHANGED|EVIDENCE_INCOMPLETE)["']/);
      expect(source, rel(file)).not.toMatch(/fingerprints?\.\w+\s*[!=]==\s*\w+\.fingerprints?\.\w+/);
      expect(source, rel(file)).not.toMatch(/\.manifest\s*[!=]==|\.result\s*[!=]==/);
    }
  });

  it("no JSON diff, deep-equality or stringify-compare helper is used or imported", () => {
    for (const file of recordFiles) {
      const source = code(read(file));
      expect(source, rel(file)).not.toMatch(/JSON\.stringify|deepEqual|isEqual|structuredClone|diff\(|jsondiff|fast-deep-equal/i);
    }
  });

  it("the verification decoder only validates coherence; it never assigns a status", () => {
    const source = code(read(join(SRC, "lib/api/decoders/records.ts")));
    expect(source).not.toMatch(/status\s*=\s*/);
    expect(source).not.toMatch(/\.status\s*=\s*["']/);
  });

  it("the changed-path module holds paths only: no values, no comparison", () => {
    const source = code(read(join(SRC, "lib/changed-paths.ts")));
    expect(source).not.toMatch(/JSON|Number|\.value\b|parseFloat|Decimal/);
  });
});

describe("exact strings stay strings in the record layer and the stored-result adapters", () => {
  const COERCION = [/\bNumber\(/, /Number\.parse(Float|Int)/, /\bparseFloat\(/, /\bparseInt\(/, /\.toFixed\(/, /\bMath\./, /(^|[=(,:?&|!\[])\s*\+\s*[A-Za-z_(]/m];
  const layer = recordFiles.filter((f) => !rel(f).startsWith("app/"));

  it.each(layer.map(rel))("%s has no numeric coercion", (name) => {
    const source = code(read(join(SRC, name)));
    for (const pattern of COERCION) expect(source, `${name}: ${pattern}`).not.toMatch(pattern);
  });

  it("the stored-result adapter never does arithmetic on a figure", () => {
    const source = code(read(join(SRC, "lib/stored-result.ts")));
    expect(source).not.toMatch(/(return_value|benchmark_return|abnormal_return|eps_change|percentage_change|value)\s*[-+*/]\s*[A-Za-z(]/);
    expect(source).not.toMatch(/[A-Za-z)]\s*[-+*/]\s*(return_value|benchmark_return|abnormal_return|eps_change|percentage_change)\b/);
  });
});

describe("the baseline and the live workspace stay separate", () => {
  it("no saved-research file requests the live observation or the benchmark index", () => {
    for (const file of recordFiles) {
      const source = code(read(file));
      expect(source, rel(file)).not.toMatch(/getObservation|getBenchmarks|decodeObservation|apiObservationsPath|apiBenchmarksPath|\/research\/observations/);
    }
  });

  it("only the re-run link builder imports the workspace's address builder, and it carries no record identity", () => {
    const importers = recordFiles.filter((f) => /@\/lib\/research-view/.test(read(f))).map(rel);
    expect(importers).toEqual(["lib/record-view.ts"]);
  });

  it("the live workspace never says 'baseline' about a live study", () => {
    for (const file of files.filter((f) => rel(f).startsWith("components/research/") || rel(f).startsWith("app/research/events/"))) {
      expect(code(read(file)), rel(file)).not.toMatch(/stored baseline|saved baseline/i);
    }
  });

  it("the verification boundary is a keyed Suspense, and verification is only reached through an explicit flag", () => {
    const entry = code(read(join(SRC, "components/records/verification-entry.tsx")));
    expect(entry).toMatch(/<Suspense key=\{`\$\{recordId\}:verify`\}/);
    expect(entry).toMatch(/state\.verify \?/);
    const detail = code(read(join(SRC, "components/records/record-detail.tsx")));
    expect(detail).not.toMatch(/VerificationRegion|verifyRecord/);
  });

  it("the saved pages call only the record reads (no event, filing or observation read in the detail page)", () => {
    const detail = code(read(join(SRC, "app/research/saved/[recordId]/page.tsx")));
    expect(detail).not.toMatch(/getEarnings|getFiling|getObservation/);
    const list = code(read(join(SRC, "components/records/records-results.tsx")));
    expect(list).not.toMatch(/getRecord\(|verifyRecord|getObservation/);
  });
});

describe("server bias", () => {
  it("only the copy action is a client component among the saved-research files", () => {
    const clients = recordFiles.filter((f) => /^["']use client["']/m.test(read(f))).map(rel);
    expect(clients).toEqual(["components/ui/copy-value.tsx"]);
  });

  it("no raw JSON dump of the manifest or the result is rendered", () => {
    for (const file of recordFiles) {
      expect(code(read(file)), rel(file)).not.toMatch(/<pre\b|dangerouslySetInnerHTML|JSON\.stringify/);
    }
  });

  it("no new dependency was added for any of this", () => {
    const pkg = JSON.parse(read(resolve(SRC, "../package.json"))) as { dependencies: Record<string, string> };
    expect(Object.keys(pkg.dependencies).sort()).toEqual(["next", "react", "react-dom"]);
  });
});
