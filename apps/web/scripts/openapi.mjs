#!/usr/bin/env node
/**
 * Frontend-owned OpenAPI contract tooling (no backend source is touched).
 *
 *   node scripts/openapi.mjs export   regenerate contract/openapi.json from the backend app,
 *                                     then src/types/generated/api.d.ts from that snapshot
 *   node scripts/openapi.mjs check    fail when the backend schema, the committed snapshot and the
 *                                     generated types disagree (writes nothing)
 *   node scripts/openapi.mjs types    regenerate only the types from the committed snapshot
 *
 * The snapshot is produced by the backend's own `create_app().openapi()` through
 * scripts/export_openapi.py, with the process environment neutralised, so the same schema always
 * yields the same bytes.
 */

import { writeFileSync } from "node:fs";

import {
  SNAPSHOT_PATH,
  TYPES_PATH,
  exportBackendSnapshot,
  readText,
  typesFromSnapshot,
} from "./openapi-lib.mjs";

function backend() {
  try {
    return exportBackendSnapshot();
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exit(2);
  }
}

const mode = process.argv[2];

if (mode === "export") {
  const snapshot = backend();
  writeFileSync(SNAPSHOT_PATH, snapshot);
  writeFileSync(TYPES_PATH, await typesFromSnapshot(snapshot));
  process.stdout.write("Wrote contract/openapi.json and src/types/generated/api.d.ts\n");
} else if (mode === "types") {
  writeFileSync(TYPES_PATH, await typesFromSnapshot(readText(SNAPSHOT_PATH)));
  process.stdout.write("Wrote src/types/generated/api.d.ts\n");
} else if (mode === "check") {
  const fresh = backend();
  const problems = [];
  if (readText(SNAPSHOT_PATH) !== fresh) {
    problems.push("contract/openapi.json differs from the backend schema (run: npm run contract:export).");
  }
  if (readText(TYPES_PATH) !== (await typesFromSnapshot(fresh))) {
    problems.push("src/types/generated/api.d.ts is stale (run: npm run contract:export).");
  }
  if (problems.length > 0) {
    process.stderr.write(`Contract drift:\n- ${problems.join("\n- ")}\n`);
    process.exit(1);
  }
  process.stdout.write("Contract is in sync: backend schema, snapshot and generated types agree.\n");
} else {
  process.stderr.write("usage: node scripts/openapi.mjs export | check | types\n");
  process.exit(64);
}
