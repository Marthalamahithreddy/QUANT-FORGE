import { DATA, expect, settled, test, type Fixture } from "./support";

/**
 * Requests the frontend makes to the API for one document load of each surface, counted at the
 * fixture API AFTER a pause long enough for the browser's link prefetching to have happened (that
 * is where an N+1 hides: a prefetched dynamic page is rendered on the server and reads the API).
 * The shell's one health read is included in every figure. Each budget is an exact count, so a
 * surface that starts reading more must change its number here on purpose.
 */
const complete = DATA.record("complete_benchmark");

interface Budget {
  readonly name: string;
  readonly path: string;
  /** Reads that must each happen exactly once. */
  readonly reads: readonly string[];
  /** The exact total, shell included. */
  readonly total: number;
}

const BUDGET: Budget[] = [
  { name: "Overview", path: "/", reads: ["health", "health/ready", "earnings-events?limit=5", "filings?limit=5", "research/records?limit=5"], total: 5 },
  { name: "Earnings list", path: "/earnings", reads: ["health", "earnings-events?limit=50"], total: 2 },
  { name: "Earnings event", path: `/earnings/${DATA.event}`, reads: ["health", `earnings-events/${DATA.event}`, `research/observations/${DATA.event}`], total: 3 },
  { name: "Filings list", path: "/filings", reads: ["health", "filings?limit=50"], total: 2 },
  { name: "Research finder", path: "/research", reads: ["health", "earnings-events?limit=50"], total: 2 },
  { name: "Research workspace", path: `/research/events/${DATA.event}`, reads: ["health", `earnings-events/${DATA.event}`, "benchmarks", `research/observations/${DATA.event}`], total: 4 },
  { name: "Saved research record", path: `/research/saved/${complete.id}`, reads: ["health", `research/records/${complete.id}`], total: 2 },
  { name: "Saved research record, verified", path: `/research/saved/${complete.id}?verify=1`, reads: ["health", `research/records/${complete.id}`, `research/records/${complete.id}/verify`], total: 3 },
];

/**
 * Waits until the API has gone quiet: no new request for `quietMs` (default 1.2 s, longer than the
 * browser takes to start link prefetching after load), up to a ceiling. Time-based by nature, but it
 * waits for the evidence (silence) rather than for a fixed delay.
 */
async function untilQuiet(fixture: Fixture, quietMs = 1200, ceilingMs = 10_000) {
  const start = Date.now();
  let seen = -1;
  let since = Date.now();
  while (Date.now() - start < ceilingMs) {
    const count = (await fixture.log()).length;
    if (count !== seen) {
      seen = count;
      since = Date.now();
    } else if (Date.now() - since >= quietMs) return;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
}

const idOnly = (line: string) => line.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, "<id>");

for (const { name, path, reads, total } of BUDGET) {
  test(`${name}: ${total} requests`, async ({ page, fixture }) => {
    await page.goto(path);
    await settled(page);
    await untilQuiet(fixture); // viewport prefetching, if any, has happened by now
    const raw = await fixture.log();
    for (const line of raw) expect(line).toMatch(/^GET /); // reads only, ever
    const log = raw.map((line) => line.replace("GET /api/v1/", ""));

    for (const needle of reads) {
      expect(log.filter((line) => line === needle || line.startsWith(`${needle}?`) || line.startsWith(`${needle}&`)), `${name}: ${needle}`).toHaveLength(1);
    }
    expect(log, `${name}: ${log.map(idOnly).join(" | ")}`).toHaveLength(total);
    console.log(`BUDGET ${name}: ${log.length}`);
  });
}

test("Saved research list: health, one list read, and one event lookup per distinct event", async ({ page, fixture }) => {
  await page.goto("/research/saved");
  await settled(page);
  await untilQuiet(fixture);
  const log = (await fixture.log()).map((line) => line.replace("GET /api/v1/", ""));
  const lookups = log.filter((line) => /^earnings-events\/[0-9a-f-]{36}$/.test(line));
  expect(new Set(lookups).size, "an event was looked up twice").toBe(lookups.length);
  expect(lookups.length).toBeLessThanOrEqual(20);
  expect(log).toHaveLength(2 + lookups.length);
  // Never an observation, a record detail or a verification, however many rows or links are on screen.
  expect(log.some((line) => line.startsWith("research/observations") || /records\/[0-9a-f-]{36}/.test(line))).toBe(false);
  console.log(`BUDGET Saved research list: ${log.length} (${lookups.length} event lookups)`);
});

test("a stored baseline is never verified by merely being on screen", async ({ page, fixture }) => {
  await page.goto(`/research/saved/${complete.id}`);
  await settled(page);
  await untilQuiet(fixture);
  expect(await fixture.served("/verify")).toHaveLength(0);
  // Hovering and focusing the verify link, which would start a prefetch, still starts nothing.
  const verify = page.getByRole("link", { name: "Verify against current evidence" });
  await verify.hover();
  await verify.focus();
  await untilQuiet(fixture);
  expect(await fixture.served("/verify")).toHaveLength(0);
});
