import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "vitest-axe";
import { describe, expect, it, vi } from "vitest";

import { FinancialValue } from "@/components/financial/financial-value";
import { ApiStatus } from "@/components/layout/api-status";
import { AppShell } from "@/components/layout/app-shell";
import { DomainState } from "@/components/state/domain-state";
import { RefusalNotice } from "@/components/state/refusal-notice";
import { ReproducibilityBadge } from "@/components/state/reproducibility-badge";
import { StatusBadge } from "@/components/state/status-badge";
import { Callout } from "@/components/ui/callout";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Frame } from "@/components/ui/frame";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { Section } from "@/components/ui/section";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { STATE_KINDS, type NonValueState } from "@/lib/state";

/** The violations themselves are the assertion, so a failure prints the rule and the nodes. */
type AxeResults = Awaited<ReturnType<typeof axe>>;

function expectNoViolations(results: AxeResults): void {
  expect(results.violations.map((v) => ({ id: v.id, help: v.help, nodes: v.nodes.map((n) => n.html) }))).toEqual([]);
}

vi.mock("next/navigation", () => ({ usePathname: () => "/earnings" }));

/**
 * axe in jsdom checks structure, names, roles and ARIA validity. It cannot judge colour contrast
 * (jsdom has no layout), so contrast is guarded by tests/tokens.test.ts, and focus order, zoom and
 * real screen-reader behaviour are covered by the manual review. This is one layer, not the
 * whole accessibility strategy.
 */
const NON_VALUE = STATE_KINDS.filter((k): k is NonValueState => k !== "VALUE");

describe("axe: foundational components and the shell", () => {
  it("the application shell (rail, header strip, theme control, section label, status)", async () => {
    const { container } = render(
      <AppShell
        status={
          <ApiStatus
            status={{ state: "connected", health: { status: "ok", service: "quantforge-api", version: "0.1.0", environment: "local" } }}
          />
        }
      >
        <PageContainer>
          <PageHeader eyebrow="Research" title="Earnings" description="Stored observations." />
        </PageContainer>
      </AppShell>,
    );
    expectNoViolations(await axe(container));
  });

  it("the shell before any API answer, and with the API unavailable", async () => {
    const checking = render(
      <AppShell status={<ApiStatus />}>
        <p>body</p>
      </AppShell>,
    );
    expectNoViolations(await axe(checking.container));
    checking.unmount();
    const down = render(
      <AppShell status={<ApiStatus status={{ state: "unavailable", detail: "The QuantForge API could not be reached." }} />}>
        <p>body</p>
      </AppShell>,
    );
    expectNoViolations(await axe(down.container));
  });

  it("every domain state, inline and as a block", async () => {
    const { container } = render(
      <main>
        <h1>States</h1>
        {NON_VALUE.map((kind) => (
          <div key={kind}>
            <p>
              <DomainState kind={kind} reason="A reason." />
            </p>
            <RefusalNotice kind={kind} title={`Section ${kind}`} reason="A reason." />
            <StatusBadge kind={kind}>{kind}</StatusBadge>
          </div>
        ))}
        <ReproducibilityBadge level="COMPLETE" />
        <ReproducibilityBadge level="PARTIAL" />
      </main>,
    );
    expectNoViolations(await axe(container));
  });

  it("financial values in a table, including missing and refused cells", async () => {
    const { container } = render(
      <main>
        <h1>Values</h1>
        <table>
          <caption>Values</caption>
          <thead>
            <tr>
              <th scope="col">Kind</th>
              <th scope="col">Value</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>eps</td>
              <td>
                <FinancialValue value="1.5300" kind="eps" />
              </td>
            </tr>
            <tr>
              <td>ratio</td>
              <td>
                <FinancialValue value="0.125" kind="ratio" />
              </td>
            </tr>
            <tr>
              <td>missing</td>
              <td>
                <FinancialValue value={null} kind="eps" />
              </td>
            </tr>
            <tr>
              <td>refused</td>
              <td>
                <FinancialValue value={null} kind="ratio" state="REFUSED" reason="Timing not stated." />
              </td>
            </tr>
          </tbody>
        </table>
      </main>,
    );
    expectNoViolations(await axe(container));
  });

  it("empty, error, loading, section, frame and callout compositions", async () => {
    const { container } = render(
      <main>
        <h1>Page</h1>
        <EmptyState title="Nothing stored">Events appear after ingestion.</EmptyState>
        <ErrorState title="Unavailable" detail="The QuantForge API answered HTTP 503." />
        <SkeletonRegion label="Loading earnings">
          <Skeleton height="2rem" />
        </SkeletonRegion>
        <Section title="Event">
          <p>text</p>
        </Section>
        <Frame title="Descriptive" actions={<StatusBadge kind="VALUE" mode="descriptive">AVAILABLE</StatusBadge>}>
          <p>text</p>
        </Frame>
        <Callout role="note" label="Boundary">
          Descriptive data.
        </Callout>
      </main>,
    );
    expectNoViolations(await axe(container));
  });
});

describe("axe harness sanity", () => {
  it("actually reports a violation (a button without an accessible name)", async () => {
    const { container } = render(
      <main>
        <h1>x</h1>
        <button type="button" />
      </main>,
    );
    const results = await axe(container);
    expect(results.violations.map((v) => v.id)).toContain("button-name");
  });
});


describe("axe: the open mobile drawer", () => {
  it("has no violations and no duplicate ids with the rail AND the drawer in the DOM", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <AppShell status={<ApiStatus />}>
        <p>body</p>
      </AppShell>,
    );
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    expectNoViolations(await axe(container));
    const ids = [...container.querySelectorAll("[id]")].map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("axe: the research workspace and its parts", () => {
  it("the whole workspace page, with the filing evaluated (every section present)", async () => {
    const { default: ResearchEventPage } = await import("@/app/research/events/[eventId]/page");
    const { renderServer } = await import("./render-server");
    const { CAPTURE, captured } = await import("./research-fixtures");
    const c = captured("filing_evaluated");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const url = new URL(input);
        const body = url.pathname === "/api/v1/benchmarks" ? { ...CAPTURE.benchmarks, limit: 200, offset: 0 } : url.pathname.includes("/research/observations/") ? c.response : c.event_record;
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
      }),
    );
    const { container } = render(<main />);
    container.remove();
    const rendered = await renderServer(
      <AppShell status={<ApiStatus />}>{await ResearchEventPage({ params: Promise.resolve({ eventId: c.event_id }), searchParams: Promise.resolve(c.params) } as never)}</AppShell>,
    );
    expectNoViolations(await axe(rendered.container));
    vi.unstubAllGlobals();
  });

  it("the event-study chart and its table", async () => {
    const { HorizonReturnsChart } = await import("@/components/research/horizon-returns-chart");
    const { ReturnsTable } = await import("@/components/research/returns-table");
    const { captured } = await import("./research-fixtures");
    const study = captured("benchmark_selected").response.descriptive.study;
    const { container } = render(
      <main>
        <h1>Study</h1>
        <HorizonReturnsChart study={study} title="Descriptive return by horizon" />
        <ReturnsTable study={study} caption="Descriptive returns by horizon" />
      </main>,
    );
    expectNoViolations(await axe(container));
  });

  it("the open evidence drawer", async () => {
    const { EvidenceDrawer } = await import("@/components/ui/evidence-drawer");
    const user = userEvent.setup();
    const { container } = render(
      <main>
        <h1>Evidence</h1>
        <EvidenceDrawer subject="the stored event" title="Event evidence">
          <p>Fingerprint</p>
        </EvidenceDrawer>
      </main>,
    );
    await user.click(screen.getByRole("button", { name: /Evidence for/ }));
    expectNoViolations(await axe(container));
  });
});
