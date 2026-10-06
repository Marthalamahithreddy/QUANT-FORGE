import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { Observation } from "@/lib/api/schema";

type Json = Record<string, unknown>;

export interface CapturedCase {
  readonly name: string;
  readonly event_id: string;
  readonly params: Record<string, string>;
  readonly response: Observation;
  readonly event_record: Json;
}

interface Capture {
  cases: CapturedCase[];
  benchmarks: { items: Json[]; total: number; limit: number; offset: number };
  unknown_event: { status: number; body: unknown };
  rejected_horizons: { status: number; body: unknown };
}

/**
 * Real backend responses, captured by scripts/capture_observation_fixtures.py from coherent
 * stored data over real HTTP. Never edited by hand: refresh by re-running the capture.
 */
export const CAPTURE: Capture = JSON.parse(readFileSync(join(process.cwd(), "tests/contract/observation.json"), "utf8"));

export function captured(name: string): CapturedCase {
  const found = CAPTURE.cases.find((entry) => entry.name === name);
  if (!found) throw new Error(`no captured case ${name}`);
  return found;
}

/** A deep copy that tests may alter to model drift. */
export const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

export const BENCHMARK_ID = captured("benchmark_selected").response.parameters.benchmark_id as string;
export const INACTIVE_BENCHMARK_ID = captured("inactive_benchmark").response.parameters.benchmark_id as string;
export const EMPTY_BENCHMARK_ID = captured("benchmark_without_data").response.parameters.benchmark_id as string;
