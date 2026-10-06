// @vitest-environment node
/**
 * The persistent shell and the streamed readiness model, rendered through React's streaming server
 * renderer (the same Suspense machinery Next uses), not just inspected as source.
 */

import { renderToReadableStream } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import RootLayout from "@/app/layout";
import { LiveApiStatus } from "@/components/layout/live-api-status";

vi.mock("next/navigation", () => ({ usePathname: () => "/earnings" }));

const cookieJar = vi.hoisted(() => ({ values: {} as Record<string, string> }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (name in cookieJar.values ? { value: cookieJar.values[name] } : undefined) }),
}));

const HEALTH = { status: "ok", service: "quantforge-api", version: "0.1.0", environment: "local" };

afterEach(() => {
  vi.unstubAllGlobals();
  cookieJar.values = {};
});

const decoder = new TextDecoder();

/**
 * Reads what the server has flushed so far: chunks are taken until the stream goes quiet (the
 * Suspense boundary is waiting on the gated health read), which is exactly "what the reader sees
 * before the API answers". `rest` then drains the remainder.
 */
async function chunks(stream: ReadableStream<Uint8Array>) {
  const reader = stream.getReader();
  const quiet = Symbol("quiet");
  let first = "";
  // A read that lost the race is kept and awaited again: its chunk must not be dropped.
  let pending = reader.read();
  for (;;) {
    const next = await Promise.race([pending, new Promise<typeof quiet>((resolve) => setTimeout(() => resolve(quiet), 60))]);
    if (next === quiet || next.done) break;
    first += decoder.decode(next.value);
    pending = reader.read();
  }
  return {
    first,
    async rest() {
      let text = "";
      for (let next = await pending; !next.done; next = await reader.read()) {
        text += decoder.decode(next.value);
      }
      return text;
    },
  };
}

/** A health endpoint that answers only when the test lets it. */
function gatedHealth(response: () => Response | Promise<Response>) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      await gate;
      return response();
    }),
  );
  return release;
}

async function renderLayout() {
  const tree = await RootLayout({ children: <p>PAGE-CONTENT</p> } as never);
  return renderToReadableStream(tree);
}

describe("the shell is rendered by the layout and does not wait for the API", () => {
  it("paints the rail, header, page content and a Checking API chip BEFORE the health read answers", async () => {
    const release = gatedHealth(() => new Response(JSON.stringify(HEALTH), { status: 200 }));
    const stream = await renderLayout();
    const { first, rest } = await chunks(stream);

    expect(first).toContain("PAGE-CONTENT");
    expect(first).toContain('aria-label="Primary"');
    expect(first).toContain("Skip to content");
    expect(first).toContain("Checking API");
    expect(first).not.toContain("Connected");

    release();
    const remainder = await rest();
    expect(remainder).toContain("Connected");
  });

  it("an unreachable API turns the chip to Unavailable without blanking the chrome or the page", async () => {
    const release = gatedHealth(() => {
      throw new TypeError("fetch failed: connect ECONNREFUSED 127.0.0.1:8000");
    });
    const stream = await renderLayout();
    const { first, rest } = await chunks(stream);
    release();
    const all = first + (await rest());

    expect(all).toContain("PAGE-CONTENT");
    expect(all).toContain('aria-label="Primary"');
    expect(all).toContain("Unavailable");
    expect(all).not.toContain("Connected");
    expect(all).not.toMatch(/ECONNREFUSED|127\.0\.0\.1|8000/);
  });

  it("a contract failure in the health payload is Unavailable, not Connected", async () => {
    const release = gatedHealth(() => new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    const { first, rest } = await chunks(await renderLayout());
    release();
    const all = first + (await rest());
    expect(all).toContain("Unavailable");
    expect(all).not.toContain("Connected");
  });

  it("renders the shell exactly once: one rail, one header, one main landmark", async () => {
    const release = gatedHealth(() => new Response(JSON.stringify(HEALTH), { status: 200 }));
    const { first, rest } = await chunks(await renderLayout());
    release();
    const html = first + (await rest());

    expect(html.match(/<main\b/g)).toHaveLength(1);
    expect(html.match(/<header\b/g)).toHaveLength(1);
    expect(html.match(/aria-label="Primary"/g)).toHaveLength(1);
    expect(html.match(/<aside\b/g)).toHaveLength(1);
  });

  it("applies the theme and tint cookies on the server, so the first paint has no flash", async () => {
    cookieJar.values = { "qf-theme": "light", "qf-tint": "on" };
    gatedHealth(() => new Response(JSON.stringify(HEALTH), { status: 200 }))();
    const { first } = await chunks(await renderLayout());
    expect(first).toMatch(/<html[^>]*data-theme="light"/);
    expect(first).toMatch(/<html[^>]*data-tint="on"/);
  });

  it("the layout is a Server Component (only the small interactive leaves are clients)", async () => {
    const { readFileSync } = await import("node:fs");
    expect(readFileSync("src/app/layout.tsx", "utf8")).not.toMatch(/^["']use client["']/m);
  });
});

describe("LiveApiStatus", () => {
  it("never rejects: every failure kind resolves to a chip", async () => {
    for (const arrange of [
      () => vi.fn().mockRejectedValue(new TypeError("down")),
      () => vi.fn().mockResolvedValue(new Response("{}", { status: 500 })),
      () => vi.fn().mockResolvedValue(new Response("not json", { status: 200 })),
    ]) {
      vi.stubGlobal("fetch", arrange());
      await expect(LiveApiStatus()).resolves.toBeDefined();
    }
  });
});
