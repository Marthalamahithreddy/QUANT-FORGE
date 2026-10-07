import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test as base, type APIRequestContext, type Page } from "@playwright/test";

export const FIXTURE_API = "http://127.0.0.1:8765";

const contract = (name: string) => JSON.parse(readFileSync(join(process.cwd(), "tests", "contract", name), "utf8"));
const observation = contract("observation.json");
const records = contract("records.json");

/** Captured identities the journeys navigate by. Read from the captures, never retyped. */
export const DATA = {
  /** The event whose default observation refuses point-in-time and keeps the descriptive study. */
  event: observation.cases.find((c: { name: string }) => c.name === "default_no_benchmark").event_id as string,
  /** The benchmark the captured benchmark-selected observation used. */
  benchmark: observation.cases.find((c: { name: string }) => c.name === "benchmark_selected").params.benchmark_id as string,
  /** A captured benchmark with no stored bars: the stock study stays, the benchmark is refused. */
  emptyBenchmark: observation.cases.find((c: { name: string }) => c.name === "benchmark_without_data").params.benchmark_id as string,
  filing: observation.cases.find((c: { name: string }) => c.name === "filing_evaluated").params.filing_accession as string,
  record: (name: string): { id: string; eventId: string } => {
    const found = records.records.find((r: { name: string }) => r.name === name);
    return { id: found.record_id, eventId: found.event_id };
  },
};

/** The fixture API's control surface. The application never reaches it. */
export class Fixture {
  constructor(private readonly request: APIRequestContext) {}
  async reset() {
    await this.request.post(`${FIXTURE_API}/__e2e/reset`);
  }
  async fault(fault: { match: string; status?: number; body?: unknown; delayMs?: number }) {
    await this.request.post(`${FIXTURE_API}/__e2e/fault`, { data: fault });
  }
  /** Requests the API served since the last reset, e.g. `GET /api/v1/health`. */
  async log(): Promise<string[]> {
    return (await this.request.get(`${FIXTURE_API}/__e2e/log`)).json();
  }
  async served(needle: string): Promise<string[]> {
    return (await this.log()).filter((line) => line.includes(needle));
  }
}

export const test = base.extend<{ fixture: Fixture }>({
  fixture: async ({ request }, provide) => {
    const fixture = new Fixture(request);
    await fixture.reset();
    await provide(fixture);
    await fixture.reset();
  },
});

export { expect };

/** Waits until no skeleton region is still busy: the page has finished streaming. */
export async function settled(page: Page) {
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
}

/** Whole-page horizontal overflow: the one responsive failure every journey rules out. */
export async function noPageOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow, "the page must not scroll sideways").toBe(false);
}

/** No backend address, driver text or stack in what a reader can see. */
export async function expectNoInternalDetail(page: Page) {
  const text = await page.locator("body").innerText();
  expect(text).not.toMatch(/127\.0\.0\.1|localhost|:8765|https?:\/\/|Traceback|psycopg|sqlalchemy|\bSELECT\b.*\bFROM\b|DETAIL:|\n\s+at \w+ \(/);
}

/** One of the two research mode frames, by its exact name. */
export const mode = (page: Page, name: "DESCRIPTIVE" | "POINT-IN-TIME") => page.getByRole("region", { name, exact: true });

/**
 * Alerts the APPLICATION renders. Next's own (empty) route announcer is also `role="alert"`, so only
 * those inside the page region count.
 */
export const alerts = (page: Page) => page.locator('main [role="alert"]');
