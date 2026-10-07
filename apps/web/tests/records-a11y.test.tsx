/* eslint-disable @typescript-eslint/no-explicit-any -- fixtures are deliberately untyped */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

import SavedRecordPage from "@/app/research/saved/[recordId]/page";
import SavedResearchPage from "@/app/research/saved/page";
import { ApiStatus } from "@/components/layout/api-status";
import { AppShell } from "@/components/layout/app-shell";
import { VerificationBadge } from "@/components/state/verification-badge";
import { CopyValue } from "@/components/ui/copy-value";
import { VERIFICATION_STATUSES } from "@/lib/api/schema";
import { VERIFICATION_LABEL } from "@/lib/record-wording";
import { RECORDS, record } from "./records-fixtures";
import { renderServer } from "./render-server";

vi.mock("next/navigation", () => ({
  usePathname: () => "/research/saved",
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
  redirect: (to: string) => {
    throw new Error(`NEXT_REDIRECT:${to}`);
  },
}));

type AxeResults = Awaited<ReturnType<typeof axe>>;
const expectNoViolations = (results: AxeResults) =>
  expect(results.violations.map((v) => ({ id: v.id, help: v.help, nodes: v.nodes.map((n) => n.html) }))).toEqual([]);

function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const url = new URL(input);
      const parts = url.pathname.split("/");
      const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
      if (url.pathname === "/api/v1/research/records") {
        const items = (RECORDS.lists.limit_20!.body as any).items as any[];
        return json({ items, total: items.length, limit: Number(url.searchParams.get("limit")), offset: Number(url.searchParams.get("offset")) });
      }
      if (url.pathname.startsWith("/api/v1/research/records/")) {
        const found = RECORDS.records.find((entry) => entry.record_id === parts[5])!;
        const reply = parts[6] === "verify" ? found.verify! : (found.detail_after ?? found.detail);
        return json(reply.body, reply.status);
      }
      const found = RECORDS.records.find((entry) => entry.event_id === parts[4]);
      return json(found?.event_record.body ?? { detail: "x" }, found?.event_record.status ?? 404);
    }),
  );
}
beforeEach(serve);
afterEach(() => vi.unstubAllGlobals());

const inShell = async (page: unknown) => renderServer(<AppShell status={<ApiStatus />}>{await (page as Promise<never>)}</AppShell>);

describe("axe: saved research", () => {
  it("the list", async () => {
    const rendered = await inShell(SavedResearchPage({ searchParams: Promise.resolve({}) } as never));
    expectNoViolations(await axe(rendered.container));
    // every table cell and fingerprint is in the document, ids are unique
    const ids = [...rendered.container.querySelectorAll("[id]")].map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it.each(["complete_filing_accounting", "partial_accounting_not_ingested", "complete_pit_eligible"])("the record %s", async (name) => {
    const entry = record(name);
    const rendered = await inShell(SavedRecordPage({ params: Promise.resolve({ recordId: entry.record_id }), searchParams: Promise.resolve({}) } as never));
    expectNoViolations(await axe(rendered.container));
    const ids = [...rendered.container.querySelectorAll("[id]")].map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
    const headings = [...rendered.container.querySelectorAll("h1, h2")].map((h) => h.tagName);
    expect(headings.filter((tag) => tag === "H1")).toHaveLength(1);
  });

  it.each(["complete_plain", "partial_benchmark", "manifest_changed", "result_changed", "event_missing"])("the verification of %s", async (name) => {
    const entry = record(name);
    const rendered = await inShell(SavedRecordPage({ params: Promise.resolve({ recordId: entry.record_id }), searchParams: Promise.resolve({ verify: "1" }) } as never));
    expectNoViolations(await axe(rendered.container));
  });

  it("the record hierarchy is in the specified order, each section a named region", async () => {
    const entry = record("complete_plain");
    await inShell(SavedRecordPage({ params: Promise.resolve({ recordId: entry.record_id }), searchParams: Promise.resolve({}) } as never));
    const names = screen.getAllByRole("region").map((region) => region.getAttribute("aria-labelledby") && document.getElementById(region.getAttribute("aria-labelledby")!)?.textContent);
    const wanted = ["Stored baseline identity", "Stored request", "Reproducibility", "Stored result", "Evidence and provenance", "Verification", "Re-run live"];
    const positions = wanted.map((name) => names.indexOf(name));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
  });

  it("the verification control is a real link a keyboard reaches, with a plain name", async () => {
    const entry = record("complete_plain");
    await inShell(SavedRecordPage({ params: Promise.resolve({ recordId: entry.record_id }), searchParams: Promise.resolve({}) } as never));
    const link = screen.getByRole("link", { name: "Verify against current evidence" });
    link.focus();
    expect(link).toHaveFocus();
    expect(link.closest("a")).toBeTruthy();
  });
});

describe("the verification badge", () => {
  it.each(VERIFICATION_STATUSES)("%s is text with a glyph, and its meaning does not rest on colour", (status) => {
    const { container } = render(<VerificationBadge status={status} />);
    expect(container).toHaveTextContent(VERIFICATION_LABEL[status]);
    expect(container.querySelector("svg[aria-hidden='true']")).toBeTruthy();
    expect(container.querySelector(`[data-verification="${status}"]`)).toBeTruthy();
  });

  it("gives each status its own glyph and keeps the 'changed' statuses out of the danger tone", () => {
    const glyphs = new Set<string>();
    for (const status of VERIFICATION_STATUSES) {
      const { container, unmount } = render(<VerificationBadge status={status} />);
      glyphs.add(container.querySelector("svg")!.getAttribute("data-glyph")!);
      const className = container.querySelector("[data-verification]")!.className;
      expect(className, status).not.toMatch(/danger|success/);
      unmount();
    }
    expect(glyphs.size).toBe(VERIFICATION_STATUSES.length);
    // MANIFEST_CHANGED is the dashed one; EVIDENCE_INCOMPLETE shares the PARTIAL half-circle: it IS that statement.
    expect(render(<VerificationBadge status="MANIFEST_CHANGED" />).container.innerHTML).toContain("border-dashed");
    expect(render(<VerificationBadge status="EVIDENCE_INCOMPLETE" />).container.innerHTML).toContain("half-circle");
  });

  it("does not style EVIDENCE_INCOMPLETE as a failure or RESULT_CHANGED as a crash", () => {
    for (const status of ["EVIDENCE_INCOMPLETE", "RESULT_CHANGED"] as const) {
      const { container } = render(<VerificationBadge status={status} />);
      expect(container.querySelector("[role=alert]")).toBeNull();
      expect(container.innerHTML).not.toMatch(/ERROR|triangle/);
    }
  });
});

describe("the copy action", () => {
  const FP = "e745fec633754821bd058e205b405cbeac9d609988702b1bee61efc43bd6e2bc";

  it("copies the exact full value, announces success, and is named for what it copies", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<CopyValue value={FP} label="manifest fingerprint" />);
    await user.click(screen.getByRole("button", { name: "Copy manifest fingerprint" }));
    expect(writeText).toHaveBeenCalledExactlyOnceWith(FP);
    expect(screen.getByRole("status")).toHaveTextContent("Copied");
  });

  it("a failed copy says so and affects nothing else", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    render(
      <p>
        <code>{FP}</code> <CopyValue value={FP} label="manifest fingerprint" />
      </p>,
    );
    await user.click(screen.getByRole("button", { name: /Copy/ }));
    expect(screen.getByRole("status")).toHaveTextContent("Could not copy");
    expect(screen.getByText(FP)).toBeInTheDocument();
  });

  it("a missing clipboard (an insecure context) is the same graceful failure", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    render(<CopyValue value={FP} label="result fingerprint" />);
    await user.click(screen.getByRole("button", { name: /Copy/ }));
    expect(screen.getByRole("status")).toHaveTextContent("Could not copy");
  });

  it("is operable from the keyboard", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<CopyValue value={FP} label="record id" />);
    await user.tab();
    expect(screen.getByRole("button", { name: "Copy record id" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(writeText).toHaveBeenCalledWith(FP);
  });
});

describe("the saved evidence drawer", () => {
  it("opens with the stored identifiers, moves focus to Close and returns it", async () => {
    const { SavedEvidence } = await import("@/components/records/saved-evidence");
    const { decodeRecordDetail } = await import("@/lib/api/decoders/records");
    const user = userEvent.setup();
    const entry = record("complete_filing_accounting");
    const detail = decodeRecordDetail(entry.detail.body, "$");
    render(
      <main>
        <h1>Record</h1>
        <SavedEvidence evidence={detail.manifest.evidence} />
      </main>,
    );
    const trigger = screen.getByRole("button", { name: "Evidence for the baseline's stock prices" });
    const bar = detail.manifest.evidence.stock.bars[0]!.bar_id;
    // The evidence is not in the document while the drawer is closed.
    expect(document.body.textContent).not.toContain(bar);
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Stock price evidence" });
    expect(within(dialog).getByRole("button", { name: "Close evidence" })).toHaveFocus();
    expect(dialog).toHaveTextContent(bar);
    expect(dialog).toHaveTextContent("A digest of the stored session dates");
    expectNoViolations(await axe(document.body));
    // jsdom has no Esc handling for <dialog>; the platform's close event is what Esc raises (see evidence-drawer.test).
    await user.click(within(dialog).getByRole("button", { name: "Close evidence" }));
    expect(trigger).toHaveFocus();
  });
});
