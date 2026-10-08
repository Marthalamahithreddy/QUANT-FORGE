// Is this page a WORKING application page? (Release smoke, browser phase.)
//
// An HTTP 200 proves only that the web server answered. The product renders an unreachable API, a
// failed database, a contract violation or a render crash as a normal-looking page with status 200, so
// a smoke that stops at the status code passes a broken deployment. This module reads the page the way
// the design system already separates the cases, by SEMANTIC markers rather than wording:
//
//   application failure   role="alert" (ErrorState, the route error boundary, the API-process line) or
//                         data-state="ERROR" (a value that is not a valid decimal, "Not ready", "Unavailable")
//   expected domain state role="status" and data-state in REFUSED | MISSING | NOT_REQUESTED | NOT_STATED |
//                         UNAVAILABLE | AMBIGUOUS | PARTIAL | NOT_APPLICABLE -- a PIT refusal, a missing
//                         target price, an empty list. These are information, never failures.
//
// The design system reserves role="alert" and the ERROR state for application/transport failure (see
// components/state/domain-state.tsx and ui/error-state.tsx), so this is a contract, not text matching.
//
// An empty list is legitimate only when the API itself says the list is empty. Whether a page should be
// populated is therefore decided by what the backend reported (the EXPECTATION), never by the page: a
// page that shows "nothing stored" while the API holds rows is a failure, not an empty state.
//
// `inspectDocument` runs inside the browser (it is serialised into the page); `judgePage` is pure.
// Both are covered by tests/page-health.test.ts against real HTML.

/**
 * Self-contained: it is stringified and evaluated in the page, so it must not reference anything
 * outside its own body.
 */
export function inspectDocument(doc) {
  const text = (element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
  const main = doc.querySelector("main") ?? doc.body;
  const position = doc.querySelector('[data-testid="pagination-position"]');
  const positionMatch = position ? /\bof\s+([\d,]+)\b/.exec(text(position)) : null;
  return {
    title: doc.title,
    h1: [...doc.querySelectorAll("h1")].map(text),
    h2: [...doc.querySelectorAll("h2")].map(text),
    // Anything the design system marks as an application failure.
    failures: [
      ...[...doc.querySelectorAll('[role="alert"]')].map((el) => ({ marker: 'role="alert"', text: text(el).slice(0, 90) })),
      ...[...doc.querySelectorAll('[data-state="ERROR"]')].map((el) => ({ marker: 'data-state="ERROR"', text: text(el).slice(0, 90) })),
    ],
    // Expected non-value states (counted by kind) -- reported, never failed.
    domainStates: [...doc.querySelectorAll("[data-state]")].reduce((counts, el) => {
      const kind = el.getAttribute("data-state") ?? "";
      counts[kind] = (counts[kind] ?? 0) + 1;
      return counts;
    }, {}),
    badges: [...doc.querySelectorAll("[data-state]")].map((el) => ({ state: el.getAttribute("data-state") ?? "", text: text(el) })),
    rows: main.querySelectorAll("table tbody tr").length,
    positionTotal: positionMatch ? Number(positionMatch[1].replace(/,/g, "")) : null,
    charts: main.querySelectorAll('svg[role="img"]').length,
    mainTextLength: text(main).length,
  };
}

/**
 * The failures for one page, as short strings (empty when the page is healthy).
 *
 * `expect` (all optional) says what the BACKEND reported this page must show:
 *   h1 / h2        headings that must be present (substring match, case-sensitive)
 *   minRows        at least this many table body rows
 *   positionTotal  the list's "of N" must equal the API's total
 *   chart          a chart must be present
 *   badges         [{ state, text }]: a data-state element of that state whose text is exactly `text`
 */
export function judgePage(snapshot, expect = {}) {
  const problems = [];
  if (snapshot.failures.length > 0) {
    const first = snapshot.failures[0];
    problems.push(
      `application error state rendered with HTTP 200 (${snapshot.failures.length} ${first.marker} marker(s); first: "${first.text}")`,
    );
  }
  // The missing headings and rows of an error page are consequences of the one failure just reported.
  if (problems.length > 0) return problems;
  if (snapshot.mainTextLength < 40) problems.push(`the page body is empty (${snapshot.mainTextLength} characters)`);
  for (const heading of asList(expect.h1)) {
    if (!snapshot.h1.some((found) => found.includes(heading))) problems.push(`expected heading "${heading}" is missing`);
  }
  for (const heading of asList(expect.h2)) {
    if (!snapshot.h2.some((found) => found.includes(heading))) problems.push(`expected section "${heading}" is missing`);
  }
  if (expect.minRows !== undefined && snapshot.rows < expect.minRows) {
    problems.push(`expected at least ${expect.minRows} row(s) because the API reports data, found ${snapshot.rows}`);
  }
  if (expect.positionTotal !== undefined && snapshot.positionTotal !== expect.positionTotal) {
    problems.push(`list position says ${snapshot.positionTotal ?? "nothing"} stored, the API reports ${expect.positionTotal}`);
  }
  if (expect.chart && snapshot.charts < 1) problems.push("expected a chart and found none");
  for (const badge of expect.badges ?? []) {
    if (!snapshot.badges.some((found) => found.state === badge.state && found.text === badge.text)) {
      problems.push(`expected a ${badge.state} "${badge.text}" indicator and found none`);
    }
  }
  return problems;
}

const asList = (value) => (value === undefined ? [] : Array.isArray(value) ? value : [value]);
