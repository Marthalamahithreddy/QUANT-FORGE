// @vitest-environment node
/**
 * The stored baseline never waits for a verification: the verification region is its own keyed
 * Suspense boundary (React's streaming renderer, the same machinery Next uses), so a slow
 * verification only delays itself.
 */

import { renderToReadableStream } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RecordDetailView } from "@/components/records/record-detail";
import { decodeRecordDetail } from "@/lib/api/decoders/records";
import { parseRecordView } from "@/lib/record-view";
import { jsonResponse } from "./fixtures";
import { record, recordDetail } from "./records-fixtures";

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

const stored = () => decodeRecordDetail(recordDetail("complete_plain"), "$");

describe("verification streams behind the stored baseline", () => {
  it("flushes the whole baseline and a loading state while the verification is pending, then the status", async () => {
    const entry = record("complete_plain");
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const fetchMock = vi.fn(async () => {
      await gate;
      return jsonResponse(entry.verify!.body);
    });
    vi.stubGlobal("fetch", fetchMock);

    const state = parseRecordView(entry.record_id, { verify: "1" });
    const stream = await renderToReadableStream(<RecordDetailView record={stored()} state={state} />);
    const { first, rest } = await read(stream);

    // Everything the baseline needs is already there...
    expect(first).toContain("Stored baseline");
    expect(first).toContain("Stored request");
    expect(first).toContain("Stored result");
    expect(first).toContain("Evidence and provenance");
    expect(first).toContain("Re-run this request live");
    // ...while the verification is still a loading region and no status has been shown.
    expect(first).toContain("Verifying this baseline against the evidence stored now");
    // The loading region announces itself politely.
    expect(first).toMatch(/role="status" aria-busy="true"[^>]*><span class="sr-only">Verifying this baseline/);
    expect(first).not.toContain("data-verification");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    release();
    expect(await rest()).toContain('data-verification="MATCH"');
  });

  it("without ?verify=1 no verification is requested at all", async () => {
    const entry = record("complete_plain");
    const fetchMock = vi.fn(async () => jsonResponse(entry.verify!.body));
    vi.stubGlobal("fetch", fetchMock);
    const stream = await renderToReadableStream(<RecordDetailView record={stored()} state={parseRecordView(entry.record_id, {})} />);
    const { first, rest } = await read(stream);
    const all = first + (await rest());
    expect(all).toContain("Verify against current evidence");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a failed verification leaves the baseline as it was", async () => {
    const entry = record("complete_plain");
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "x" }, 500)));
    const stream = await renderToReadableStream(<RecordDetailView record={stored()} state={parseRecordView(entry.record_id, { verify: "1" })} />);
    const { first, rest } = await read(stream);
    const all = first + (await rest());
    expect(all).toContain("Stored result");
    expect(all).toContain("is not a verification result");
    expect(all).not.toContain("data-verification");
  });
});
