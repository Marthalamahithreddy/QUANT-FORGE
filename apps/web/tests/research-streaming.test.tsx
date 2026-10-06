// @vitest-environment node
/**
 * The study never waits for the benchmark index: the index only NAMES a benchmark the reader
 * already selected, so it streams in its own boundary (React's streaming renderer, the same
 * Suspense machinery Next uses).
 */

import { renderToReadableStream } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchResults } from "@/components/research/research-results";
import { parseResearchConfig } from "@/lib/research-view";
import { jsonResponse } from "./fixtures";
import { BENCHMARK_ID, CAPTURE, captured } from "./research-fixtures";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

afterEach(() => vi.unstubAllGlobals());

const decoder = new TextDecoder();

async function read(stream: ReadableStream<Uint8Array>) {
  const reader = stream.getReader();
  const quiet = Symbol("quiet");
  let first = "";
  let pending = reader.read();
  for (;;) {
    const next = await Promise.race([pending, new Promise<typeof quiet>((resolve) => setTimeout(() => resolve(quiet), 80))]);
    if (next === quiet || next.done) break;
    first += decoder.decode(next.value);
    pending = reader.read();
  }
  return {
    first,
    async rest() {
      let text = "";
      for (let next = await pending; !next.done; next = await reader.read()) text += decoder.decode(next.value);
      return text;
    },
  };
}

describe("a slow benchmark index does not hold back the study", () => {
  it("flushes the whole study with the benchmark's id first, then names it when the index answers", async () => {
    const c = captured("benchmark_selected");
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const url = new URL(input);
        if (url.pathname === "/api/v1/benchmarks") {
          await gate;
          return jsonResponse({ ...CAPTURE.benchmarks, limit: 200, offset: 0 });
        }
        return jsonResponse(c.response);
      }),
    );
    const config = parseResearchConfig(c.event_id, c.params);
    const stream = await renderToReadableStream(<ResearchResults eventId={c.event_id} record={c.event_record as never} config={config} />);
    const { first, rest } = await read(stream);

    expect(first).toContain("Descriptive returns by horizon"); // the table is already there
    expect(first).toContain("Refusal ledger");
    expect(first).toContain(BENCHMARK_ID); // the benchmark is shown by id while its name is pending
    expect(first).not.toContain("Observation Index");

    release();
    expect(await rest()).toContain("Observation Index (OBSI)");
  });

  it("when the index never resolves into a name, the study and the id remain", async () => {
    const c = captured("benchmark_selected");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => (new URL(input).pathname === "/api/v1/benchmarks" ? jsonResponse({ detail: "x" }, 503) : jsonResponse(c.response))),
    );
    const config = parseResearchConfig(c.event_id, c.params);
    const stream = await renderToReadableStream(<ResearchResults eventId={c.event_id} record={c.event_record as never} config={config} />);
    const { first, rest } = await read(stream);
    const all = first + (await rest());
    expect(all).toContain("Descriptive returns by horizon");
    expect(all).toContain(BENCHMARK_ID);
    expect(all).not.toContain("Observation Index");
  });
});
