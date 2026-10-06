import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EarningsTable } from "@/components/earnings/earnings-table";
import { makeEvent } from "./fixtures";

const SEC = makeEvent({
  id: "sec",
  company: { ...makeEvent().company, display_name: "Sec Co" },
  availability: {
    observed_at: "2024-03-15T11:00:00Z",
    observed_at_basis: "SEC_FILING_ACCEPTANCE",
    pit_eligibility: "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS",
    pit_reason: "basis not approved",
  },
});
const ELIGIBLE = makeEvent({
  id: "ok",
  company: { ...makeEvent().company, display_name: "Pub Co" },
  availability: {
    observed_at: "2024-03-15T11:00:00.5Z",
    observed_at_basis: "SOURCE_PUBLICATION",
    pit_eligibility: "ELIGIBLE",
    pit_reason: "ok",
  },
});
const NONE = makeEvent({ id: "none", company: { ...makeEvent().company, display_name: "Dark Co" } });

const bodyRows = () => screen.getAllByRole("row").slice(1);

describe("EarningsTable", () => {
  it("is one semantic table with a caption, scoped column headers and an identity row header", () => {
    render(<EarningsTable events={[NONE]} />);
    const table = screen.getByRole("table", { name: "Earnings events" });
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual([
      "Event date",
      "Company",
      "Ticker",
      "Reported EPS",
      "Est. EPS",
      "EPS surprise",
      "PIT status",
      "Research",
      "Timing",
      "Observed at",
      "Basis",
      "Source",
    ]);
    for (const header of within(table).getAllByRole("columnheader")) {
      expect(header).toHaveAttribute("scope", "col");
    }
    expect(within(bodyRows()[0]!).getByRole("rowheader")).toHaveTextContent("2024-03-15");
  });

  it("has no sort buttons, no sort state and no row filters: order and filters are the API's", () => {
    render(<EarningsTable events={[SEC, ELIGIBLE, NONE]} />);
    const table = screen.getByRole("table");
    expect(within(table).queryAllByRole("button")).toHaveLength(0);
    for (const header of within(table).getAllByRole("columnheader")) {
      expect(header).not.toHaveAttribute("aria-sort");
    }
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.queryByLabelText(/Filter loaded rows/)).toBeNull();
    expect(bodyRows().map((row) => within(row).getByRole("rowheader").textContent)).toHaveLength(3);
    // The rows keep the order they were given.
    expect(bodyRows().map((row) => row.textContent)).toEqual([
      expect.stringContaining("Sec Co"),
      expect.stringContaining("Pub Co"),
      expect.stringContaining("Dark Co"),
    ]);
  });

  it("shows decimal strings exactly as sent, right-aligned with tabular figures", () => {
    render(<EarningsTable events={[makeEvent({ reported_eps: "0.3000", estimated_eps: "0.1000" })]} />);
    const reported = screen.getByText("0.3000");
    expect(reported).toBeInTheDocument();
    expect(screen.getByText("0.1000")).toBeInTheDocument();
    expect(reported.closest("td")).toHaveClass("text-right", "num");
  });

  it("states a null EPS as not stated, never as zero", () => {
    render(<EarningsTable events={[makeEvent({ reported_eps: null, estimated_eps: null })]} />);
    const row = bodyRows()[0]!;
    expect(within(row).getAllByText(/Not stated/).length).toBeGreaterThanOrEqual(2);
    // The two EPS cells (4th and 5th) say so in words and show no digit at all.
    const cells = within(row).getAllByRole("cell");
    for (const cell of [cells[2]!, cells[3]!]) expect(cell.textContent).not.toMatch(/\d/);
  });

  it("states missing availability explicitly", () => {
    render(<EarningsTable events={[NONE]} />);
    const row = bodyRows()[0]!;
    expect(within(row).getByText(/did not supply a historical availability time/)).toBeInTheDocument();
    expect(within(row).getByText("No availability evidence")).toBeInTheDocument();
    expect(within(row).getByText("Not PIT eligible")).toBeInTheDocument();
  });

  it("labels SEC acceptance as such and never as publication or release time", () => {
    render(<EarningsTable events={[SEC]} />);
    expect(screen.getByText("SEC filing acceptance")).toBeInTheDocument();
    expect(screen.getByText("Basis not approved")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/market publication|release time|first public/i);
  });

  it("shows an eligible event as PIT eligible with its UTC observed_at, in the PIT mode style", () => {
    render(<EarningsTable events={[ELIGIBLE]} />);
    const badge = within(screen.getByRole("table")).getByText("PIT eligible");
    expect(badge.closest("[data-mode]")).toHaveAttribute("data-mode", "pit");
    expect(screen.getByText("2024-03-15 11:00:00.5 UTC")).toBeInTheDocument();
    expect(screen.getByText("Source publication")).toBeInTheDocument();
  });

  it("does not paint a refused point-in-time status as an error or as a success", () => {
    render(<EarningsTable events={[NONE]} />);
    const badge = within(screen.getByRole("table")).getByText("Not PIT eligible").closest("[data-state]");
    expect(badge).toHaveAttribute("data-state", "REFUSED");
    expect(badge?.className).not.toMatch(/danger|success/);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("puts the link in the first cell only: no whole-row click target", () => {
    render(<EarningsTable events={[ELIGIBLE]} />);
    const row = bodyRows()[0]!;
    expect(row).not.toHaveAttribute("onclick");
    expect(row).not.toHaveAttribute("tabindex");
    expect(within(within(row).getByRole("rowheader")).getByRole("link", { name: "2024-03-15" })).toHaveAttribute(
      "href",
      "/earnings/ok",
    );
  });

  it("gives the Research action the event's identity in its path and in its accessible name", () => {
    render(<EarningsTable events={[ELIGIBLE]} />);
    const link = screen.getByRole("link", { name: /Research Pub Co, 2024-03-15/ });
    expect(link).toHaveAttribute("href", "/research/events/ok");
  });

  it("shows the tickers the backend proved for the event date, not a current or listed one", () => {
    const listedOnly = makeEvent({ resolved_tickers: [] });
    render(<EarningsTable events={[listedOnly]} />);
    expect(screen.getByText("Not resolved")).toBeInTheDocument();
    expect(screen.queryByText("ACME")).toBeNull();
  });

  it("shows every proven ticker", () => {
    render(<EarningsTable events={[makeEvent({ resolved_tickers: ["AAA", "BBB"] })]} />);
    expect(screen.getByText("AAA, BBB")).toBeInTheDocument();
  });

  describe("EPS surprise", () => {
    it("shows the signed exact figure with its ratio as a percent beneath", () => {
      render(<EarningsTable events={[makeEvent()]} />);
      const cell = screen.getByText("+0.1000").closest("td")!;
      expect(cell).toHaveTextContent("+10.00%");
    });

    it.each([
      ["MISSING_REPORTED_EPS", "NOT_STATED"],
      ["MISSING_ESTIMATED_EPS", "NOT_STATED"],
      ["NOT_POINT_IN_TIME_ELIGIBLE", "REFUSED"],
    ] as const)("renders %s as the %s state with the backend's reason", (status, state) => {
      const event = makeEvent({
        reported_eps: null,
        eps_surprise: { status, absolute: null, ratio: null, reason: "a reason from the backend" },
      });
      render(<EarningsTable events={[event]} />);
      // The surprise cell is the 5th data cell (the first column is the row header).
      const mark = within(bodyRows()[0]!).getAllByRole("cell")[4]!.querySelector(`[data-state="${state}"]`);
      expect(mark).not.toBeNull();
      expect(mark).toHaveAttribute("title", "a reason from the backend");
    });

    it("keeps the absolute figure and marks only the ratio undefined for ZERO_ESTIMATE", () => {
      const event = makeEvent({
        estimated_eps: "0.0000",
        eps_surprise: { status: "ZERO_ESTIMATE", absolute: "1.1000", ratio: null, reason: "estimate is zero" },
      });
      render(<EarningsTable events={[event]} />);
      const cell = screen.getByText("+1.1000").closest("td")!;
      expect(cell.querySelector('[data-state="UNAVAILABLE"]')).not.toBeNull();
      expect(cell.textContent).not.toMatch(/0\.00%|%/);
    });
  });

  describe("responsive column priority", () => {
    it("hides P3 below 1280px and P2 below 768px; P1 stays visible", () => {
      render(<EarningsTable events={[NONE]} />);
      const classOf = (name: string) => screen.getByRole("columnheader", { name }).className;
      for (const p1 of ["Event date", "Company", "Ticker", "Reported EPS", "Est. EPS", "EPS surprise", "PIT status", "Research"]) {
        expect(classOf(p1)).not.toMatch(/\bhidden\b/);
      }
      expect(classOf("Timing")).toMatch(/hidden md:table-cell/);
      for (const p3 of ["Observed at", "Basis", "Source"]) expect(classOf(p3)).toMatch(/hidden xl:table-cell/);
    });

    it("says how many columns are hidden at each width, and does not turn rows into cards", () => {
      render(<EarningsTable events={[NONE]} />);
      expect(screen.getByTestId("columns-note-narrow")).toHaveTextContent("4 more columns at wider widths");
      expect(screen.getByTestId("columns-note-medium")).toHaveTextContent("3 more columns at wider widths");
      expect(screen.getAllByRole("row")).toHaveLength(2);
    });
  });
});
