import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import FilingPage, { generateMetadata as filingMetadata } from "@/app/filings/[accession]/page";
import { generateMetadata as earningsMetadata } from "@/app/earnings/[id]/page";
import { generateMetadata as recordMetadata } from "@/app/research/saved/[recordId]/page";
import { generateMetadata as researchMetadata } from "@/app/research/events/[eventId]/page";
import RouteError from "@/app/error";
import NotFound from "@/app/not-found";
import EarningsNotFound from "@/app/earnings/not-found";
import ResearchEventNotFound from "@/app/research/events/[eventId]/not-found";
import SavedRecordNotFound from "@/app/research/saved/[recordId]/not-found";
import { HeaderContext } from "@/components/layout/header-context";
import { PitBadge } from "@/components/state/pit-badge";
import { PIT_ELIGIBILITIES } from "@/lib/api/schema";
import { PIT_PRESENTATION } from "@/lib/availability";
import { jsonResponse, makeEvent } from "./fixtures";

const pathname = vi.hoisted(() => ({ value: "/" }));
vi.mock("next/navigation", () => ({
  usePathname: () => pathname.value,
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
  redirect: (to: string) => {
    throw new Error(`NEXT_REDIRECT:${to}`);
  },
}));

afterEach(() => vi.unstubAllGlobals());

const ID = "11111111-1111-4111-8111-111111111111";

describe("one point-in-time badge everywhere", () => {
  it("renders each classification with the label the vocabulary names, dashed and glyphed", () => {
    for (const eligibility of PIT_ELIGIBILITIES) {
      const { container, unmount } = render(<PitBadge eligibility={eligibility} />);
      const badge = container.querySelector("[data-state]") as HTMLElement;
      expect(badge).toHaveTextContent(PIT_PRESENTATION[eligibility].label);
      // A point-in-time classification carries the point-in-time mode; "not applicable" is not one.
      if (eligibility === "NOT_APPLICABLE") expect(badge.dataset.mode).toBeUndefined();
      else expect(badge.dataset.mode).toBe("pit");
      unmount();
    }
  });

  it("is the only place a classification becomes a badge, and four surfaces use it", () => {
    const root = join(process.cwd(), "src");
    const files = (readdirSync(root, { recursive: true }) as string[]).filter((f) => /\.tsx$/.test(f)).map((f) => join(root, f));
    const users = files.filter((f) => /PIT_PRESENTATION\[[^\]]+\]\.label/.test(readFileSync(f, "utf8"))).map((f) => relative(root, f));
    expect(users).toEqual(["components/state/pit-badge.tsx"]);
    const badgeFiles = files.filter((f) => /<PitBadge/.test(readFileSync(f, "utf8"))).map((f) => relative(root, f)).sort();
    expect(badgeFiles).toEqual([
      "components/earnings/earnings-table.tsx",
      "components/earnings/event-detail.tsx",
      "components/overview/latest-earnings.tsx",
      "components/research/event-finder.tsx",
    ]);
  });
});

describe("page titles read {identity} · {surface}", () => {
  it("names an earnings event, a Research study, a filing and a saved record the same way", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ ...makeEvent({ id: ID, event_date: "2024-03-15" }) }))));
    expect(await earningsMetadata({ params: Promise.resolve({ id: ID }) } as never)).toEqual({ title: "Acme Corp · 2024-03-15 · Earnings" });
    expect(await researchMetadata({ params: Promise.resolve({ eventId: ID }) } as never)).toEqual({ title: "Acme Corp · 2024-03-15 · Research" });
    expect(await filingMetadata({ params: Promise.resolve({ accession: "0000320193-24-000010" }) } as never)).toEqual({ title: "0000320193-24-000010 · Filing" });
    expect(await recordMetadata({ params: Promise.resolve({ recordId: ID }) } as never)).toEqual({ title: "Record 11111111 · Saved research" });
  });

  it("falls back to the surface name, never to an invented identity", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ detail: "down" }, 503))));
    expect(await earningsMetadata({ params: Promise.resolve({ id: ID }) } as never)).toEqual({ title: "Earnings event" });
    expect(await researchMetadata({ params: Promise.resolve({ eventId: ID }) } as never)).toEqual({ title: "Research" });
    expect(await recordMetadata({ params: Promise.resolve({ recordId: "not-a-uuid" }) } as never)).toEqual({ title: "Saved research record" });
    void FilingPage;
  });
});

describe("the location strip", () => {
  it("does not say 'Research / Research', and names a group only when it adds something", () => {
    for (const [path, text] of [
      ["/", "Overview"],
      ["/earnings", "Evidence / Earnings"],
      ["/filings/0000320193-24-000010", "Evidence / Filings"],
      ["/research", "Research"],
      ["/research/events/abc", "Research"],
      ["/research/saved", "Research / Saved research"],
      ["/research/saved/abc", "Research / Saved research"],
    ] as const) {
      pathname.value = path;
      const { unmount } = render(<HeaderContext />);
      expect(screen.getByTestId("header-context").textContent, path).toBe(text);
      unmount();
    }
  });
});

describe("a page that is only a state still has exactly one h1", () => {
  it("not-found routes and the error boundary", () => {
    for (const [name, element] of [
      ["not-found", <NotFound key="a" />],
      ["earnings not-found", <EarningsNotFound key="b" />],
      ["research event not-found", <ResearchEventNotFound key="c" />],
      ["saved record not-found", <SavedRecordNotFound key="d" />],
      ["error boundary", <RouteError key="e" error={new Error("x")} retry={() => undefined} />],
    ] as const) {
      const { unmount } = render(element);
      expect(screen.getAllByRole("heading", { level: 1 }), name).toHaveLength(1);
      unmount();
    }
  });
});

describe("one name for one thing, in production copy", () => {
  const root = join(process.cwd(), "src");
  const sources = (readdirSync(root, { recursive: true }) as string[])
    .filter((f) => /\.tsx?$/.test(f) && !f.includes("types/generated"))
    .map((f) => [f, readFileSync(join(root, f), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "")] as const);

  it("uses none of the retired names", () => {
    const retired: [RegExp, string][] = [
      [/PIT status/, "the column is 'Point-in-time'"],
      [/Open study|Open research workspace/, "the link to the workspace is 'Research' / 'Open Research'"],
      [/stored observation|Stored observation|earnings observation|Earnings observation/, "an earnings row is an 'event'"],
      [/[Ss]aved baseline/, "a saved item is a 'saved research record'; its content is a 'stored baseline'"],
      [/saved as a baseline/, "the live workspace does not speak of baselines"],
    ];
    for (const [file, text] of sources) {
      for (const [pattern, why] of retired) expect(text, `${file}: ${why}`).not.toMatch(pattern);
    }
  });
});

describe("links inside running text are not told apart by colour alone", () => {
  it("underlines a link in a paragraph, a note or a status (and not the navigation or a list of links), in the components layer so a utility can override", () => {
    const css = readFileSync(join(process.cwd(), "src/app/globals.css"), "utf8");
    expect(css).toMatch(/:where\(p, \[role="status"\], \[role="alert"\], \.type-meta, \.type-body, \.qf-prose\) a \{\s*text-decoration-line: underline;/);
  });
});
