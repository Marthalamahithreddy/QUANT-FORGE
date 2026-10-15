/**
 * Capture the real Companies API responses used by the fixture-backed browser tests.
 *
 *   node scripts/capture-companies.mjs http://127.0.0.1:8000
 *
 * Reads GET /api/v1/companies and each stored company's /financials from a running API (the
 * validation database) and writes tests/contract/companies.json byte for byte: nothing is edited,
 * generated or invented. It also records the API's own 404 body for an unknown CIK. Only GET is issued.
 */

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const base = (process.argv[2] ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const out = join(dirname(fileURLToPath(import.meta.url)), "..", "tests", "contract", "companies.json");
const UNKNOWN_CIK = "0000000001";

async function get(path) {
  const response = await fetch(`${base}${path}`);
  return { status: response.status, body: await response.json() };
}

const directory = await get("/api/v1/companies?limit=50&offset=0");
if (directory.status !== 200) throw new Error(`directory answered ${directory.status}`);

const financials = {};
for (const item of directory.body.items) {
  const reply = await get(`/api/v1/companies/${item.cik}/financials`);
  if (reply.status !== 200) throw new Error(`${item.cik} answered ${reply.status}`);
  financials[item.cik] = reply.body;
}
const unknown = await get(`/api/v1/companies/${UNKNOWN_CIK}/financials`);
if (unknown.status !== 404) throw new Error(`an unknown CIK answered ${unknown.status}`);

writeFileSync(out, `${JSON.stringify({ directory: directory.body, financials, unknown_company: { cik: UNKNOWN_CIK, ...unknown } }, null, 1)}\n`);
console.log(`wrote ${out}: ${directory.body.items.length} companies`);
