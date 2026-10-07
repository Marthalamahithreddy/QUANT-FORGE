import { readFileSync } from "node:fs";
import { join } from "node:path";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any -- captured payloads are deliberately untyped here

export interface Reply {
  readonly status: number;
  readonly body: Json | null;
}

export interface CapturedRecord {
  readonly name: string;
  readonly record_id: string;
  readonly event_id: string;
  readonly detail: Reply;
  readonly detail_after?: Reply;
  readonly verify?: Reply;
  readonly event_record: Reply;
  readonly event_record_after?: Reply;
}

interface Capture {
  records: CapturedRecord[];
  empty_list: Reply;
  lists: Record<string, Reply>;
  unknown_record: { record_id: string; detail: Reply; verify: Reply };
  malformed_id: { detail: Reply; verify: Reply };
}

/**
 * Real backend responses for saved research records, captured by scripts/capture_record_fixtures.py
 * (the backend's own scenario builders, its own save service, real PostgreSQL, real HTTP). Never
 * edited by hand: refresh by re-running the capture. Manifests and results are NOT simplified.
 */
export const RECORDS: Capture = JSON.parse(readFileSync(join(process.cwd(), "tests/contract/records.json"), "utf8"));

export function record(name: string): CapturedRecord {
  const found = RECORDS.records.find((entry) => entry.name === name);
  if (!found) throw new Error(`no captured record ${name}`);
  return found;
}

export const recordDetail = (name: string): Json => record(name).detail.body as Json;
export const recordVerify = (name: string): Json => record(name).verify!.body as Json;

export const COMPLETE_NAMES = ["complete_plain", "complete_benchmark", "complete_filing_accounting", "complete_pit_eligible", "complete_default_horizons"];

/** A deep copy that tests may alter to model drift. */
export const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
