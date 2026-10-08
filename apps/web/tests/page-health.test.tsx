import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import RouteError from "@/app/error";
import { DomainState } from "@/components/state/domain-state";
import { RefusalNotice } from "@/components/state/refusal-notice";
import { StatusBadge } from "@/components/state/status-badge";
import { ApiFailureState } from "@/components/ui/api-failure";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { FilterErrors } from "@/components/ui/filter-errors";
import { ListEmpty } from "@/components/ui/list-empty";
import { RecordFailure } from "@/components/records/record-failure";
import type { ApiFailure } from "@/lib/api/result";
import { STATE_KINDS, type NonValueState } from "@/lib/state";

import { inspectDocument, judgePage } from "../../../scripts/validation/page_health.mjs";

/**
 * The release smoke's page classifier (scripts/validation/page_health.mjs), run against the REAL
 * components the product renders. The smoke must tell an application failure served with HTTP 200
 * from the domain states the product legitimately shows (a refusal, a missing value, an empty list),
 * using the design system's own semantic markers -- so these tests render the actual components, not
 * hand-written HTML that could drift from them.
 */
const PADDING = "A page body long enough to be a real page, with context around the state under test. ";

function snapshotOf(content: ReactNode, extra = "") {
  const { container } = render(
    <main>
      <h1>Page</h1>
      <p>{PADDING}</p>
      {content}
      {extra ? <p>{extra}</p> : null}
    </main>,
  );
  return inspectDocument(container.ownerDocument);
}
const problemsOf = (content: ReactNode, expectation = {}) => judgePage(snapshotOf(content), expectation);

const FAILURES: readonly (readonly [string, ApiFailure])[] = [
  ["the API is unreachable", { kind: "unreachable", timedOut: false }],
  ["the request timed out", { kind: "unreachable", timedOut: true }],
  ["the database is down (HTTP 503)", { kind: "unavailable" }],
  ["an unexpected server error", { kind: "server_error", status: 500 }],
  ["an invalid response (contract drift)", { kind: "contract", path: "$.items[0].id", expected: "a string" }],
  ["a rejected request", { kind: "rejected", issues: [{ where: "query.horizons", message: "must be 1-20" }] }],
  ["not found", { kind: "not_found" }],
];

describe("page health: an application failure served with HTTP 200 is a failure", () => {
  it.each(FAILURES)("%s (ApiFailureState)", (_name, failure) => {
    const problems = problemsOf(<ApiFailureState failure={failure} subject="the earnings events" />);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toMatch(/^application error state rendered with HTTP 200 \(1 role="alert" marker/);
  });

  it("a saved-record integrity failure (HTTP 500)", () => {
    const problems = problemsOf(<RecordFailure failure={{ kind: "server_error", status: 500 }} subject="this saved record" />);
    expect(problems[0]).toContain("application error state rendered with HTTP 200");
  });

  it("the route error boundary (an unexpected rendering error)", () => {
    const problems = problemsOf(<RouteError error={Object.assign(new Error("boom"), { digest: "abc" })} retry={() => {}} />);
    expect(problems[0]).toContain("application error state rendered with HTTP 200");
    expect(problems[0]).toContain("Something went wrong rendering this page");
  });

  it("a generic ErrorState", () => {
    expect(problemsOf(<ErrorState title="Temporarily unavailable" />)[0]).toContain("application error state");
  });

  it("an ERROR domain state inside otherwise normal content (a value that is not a valid decimal)", () => {
    const problems = problemsOf(<DomainState kind="ERROR" reason="The value is not a valid decimal string." />);
    expect(problems[0]).toContain('data-state="ERROR"');
  });

  it('a "Not ready" / "Unavailable" system badge', () => {
    expect(problemsOf(<StatusBadge kind="ERROR">Not ready</StatusBadge>)[0]).toContain("application error state");
  });

  it("an ERROR refusal notice (role=alert)", () => {
    expect(problemsOf(<RefusalNotice kind="ERROR" title="Anything" />)[0]).toContain("application error state");
  });

  it("a rejected-filter notice is an error too (the smoke never visits a page with invalid parameters)", () => {
    const problems = problemsOf(<FilterErrors id="f" errors={[{ param: "limit", message: "too large" }]} />);
    expect(problems[0]).toContain("application error state");
  });

  it("reports only the failure, not the consequences (missing headings of an error page)", () => {
    const snapshot = snapshotOf(<ErrorState title="Temporarily unavailable" />);
    expect(judgePage(snapshot, { h1: "Earnings", h2: ["Stored event"], minRows: 1, chart: true })).toHaveLength(1);
  });
});

describe("page health: expected domain states are NOT failures", () => {
  const NON_ERROR = STATE_KINDS.filter((kind): kind is Exclude<NonValueState, "ERROR"> => kind !== "VALUE" && kind !== "ERROR");

  it.each(NON_ERROR)("an inline %s state", (kind) => {
    expect(problemsOf(<DomainState kind={kind} />)).toEqual([]);
  });

  it.each(NON_ERROR)("a %s refusal notice (REFUSED = the PIT refusal, MISSING = a missing target price)", (kind) => {
    expect(problemsOf(<RefusalNotice kind={kind} title="Point-in-time study" reason="no approved availability evidence" />)).toEqual([]);
  });

  it("the point-in-time refusal and a missing target price, together, with a populated page", () => {
    const content = (
      <>
        <RefusalNotice kind="REFUSED" title="Point-in-time study" reason="INELIGIBLE_NO_AVAILABILITY_EVIDENCE" />
        <DomainState kind="MISSING" reason="MISSING_TARGET_PRICE: the +5 session 2026-10-09 has no stored price" />
        <DomainState kind="UNAVAILABLE" reason="INSUFFICIENT_FORWARD_SESSIONS" />
      </>
    );
    const snapshot = snapshotOf(content);
    expect(snapshot.failures).toEqual([]);
    // the block-form refusal is role="status" (no data-state); the inline states carry data-state
    expect(snapshot.domainStates).toEqual({ MISSING: 1, UNAVAILABLE: 1 });
    expect(judgePage(snapshot, {})).toEqual([]);
  });

  it("a legitimate empty list, when the API itself reported nothing stored", () => {
    const content = <ListEmpty total={0} rows={0} filtered={false} clearHref="/x" firstPageHref="/x" noun="events" noData={{ title: "No events", body: "Nothing is stored." }} noMatch={{ title: "None", body: "no" }} />;
    expect(problemsOf(content, { minRows: 0 })).toEqual([]);
  });
});

describe("page health: a page must show what the API reported it holds", () => {
  const table = (rows: number) => (
    <>
      <table>
        <tbody>
          {Array.from({ length: rows }, (_, index) => (
            <tr key={index}>
              <td>row {index}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p data-testid="pagination-position">Rows 1–{rows} of {rows} stored earnings events</p>
    </>
  );

  it("passes a populated list that matches the API's total", () => {
    expect(problemsOf(table(3), { minRows: 1, positionTotal: 3 })).toEqual([]);
  });

  it("fails an EMPTY STATE where the API reported rows (missing required data)", () => {
    const empty = <EmptyState title="No earnings events are stored yet">Nothing to show.</EmptyState>;
    const problems = problemsOf(empty, { minRows: 1, positionTotal: 3 });
    expect(problems).toContain("expected at least 1 row(s) because the API reports data, found 0");
    expect(problems.some((problem) => problem.startsWith("list position says nothing stored"))).toBe(true);
  });

  it("fails a list whose position disagrees with the API's total", () => {
    expect(problemsOf(table(2), { minRows: 1, positionTotal: 3 })).toEqual(["list position says 2 stored, the API reports 3"]);
  });

  it("fails a page missing an expected heading, section or chart", () => {
    const problems = problemsOf(<h2>Stored event</h2>, { h1: "Nike, Inc.", h2: ["Stored event", "Provenance"], chart: true });
    expect(problems).toEqual(['expected heading "Nike, Inc." is missing', 'expected section "Provenance" is missing', "expected a chart and found none"]);
  });

  it("accepts a chart (svg role=img) and a Ready indicator when they are expected", () => {
    const content = (
      <>
        <svg role="img" aria-label="returns" />
        <StatusBadge kind="VALUE">Ready</StatusBadge>
      </>
    );
    expect(problemsOf(content, { chart: true, badges: [{ state: "VALUE", text: "Ready" }] })).toEqual([]);
  });

  it('fails the overview whose readiness is "Not ready" even though the status line exists', () => {
    const content = <StatusBadge kind="ERROR">Not ready</StatusBadge>;
    expect(problemsOf(content, { badges: [{ state: "VALUE", text: "Ready" }] })[0]).toContain("application error state");
  });

  it("fails a blank page", () => {
    const { container } = render(<main />);
    expect(judgePage(inspectDocument(container.ownerDocument), {})).toEqual(["the page body is empty (0 characters)"]);
  });
});
