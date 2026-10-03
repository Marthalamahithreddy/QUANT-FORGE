import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

function bodyRows() {
  return screen.getAllByRole("row").slice(1);
}

describe("EarningsTable", () => {
  it("renders a semantic table with column headers", () => {
    render(<EarningsTable events={[NONE]} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").length).toBeGreaterThanOrEqual(11);
    expect(within(table).getByRole("columnheader", { name: /Reported EPS/ })).toBeInTheDocument();
  });

  it("shows decimal strings exactly as sent", () => {
    render(<EarningsTable events={[makeEvent({ reported_eps: "0.3000", estimated_eps: "0.1000" })]} />);
    expect(screen.getByText("0.3000")).toBeInTheDocument();
    expect(screen.getByText("0.1000")).toBeInTheDocument();
  });

  it("states missing availability explicitly", () => {
    render(<EarningsTable events={[NONE]} />);
    expect(screen.getByText("Not disclosed")).toBeInTheDocument();
    expect(screen.getByText("No availability evidence")).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getByText("Not PIT eligible")).toBeInTheDocument();
  });

  it("labels SEC acceptance as such and never as publication or release time", () => {
    render(<EarningsTable events={[SEC]} />);
    expect(screen.getByText("SEC filing acceptance")).toBeInTheDocument();
    expect(screen.getByText("Basis not approved")).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/market publication|release time|first public/i);
  });

  it("shows an eligible event as PIT eligible with its UTC observed_at", () => {
    render(<EarningsTable events={[ELIGIBLE]} />);
    expect(within(screen.getByRole("table")).getByText("PIT eligible")).toBeInTheDocument();
    expect(screen.getByText("2024-03-15 11:00:00.5 UTC")).toBeInTheDocument();
    expect(screen.getByText("Source publication")).toBeInTheDocument();
  });

  it("links each event to its detail page", () => {
    render(<EarningsTable events={[ELIGIBLE]} />);
    expect(screen.getByRole("link", { name: "2024-03-15" })).toHaveAttribute(
      "href",
      "/earnings/ok",
    );
  });

  it("shows Not resolved when no listing was valid on the event date", () => {
    const event = makeEvent({
      listings: [{ ...makeEvent().listings[0]!, valid_on_event_date: false }],
    });
    render(<EarningsTable events={[event]} />);
    expect(screen.getByText("Not resolved")).toBeInTheDocument();
  });

  it("filters loaded rows by PIT status using the backend classification", async () => {
    const user = userEvent.setup();
    render(<EarningsTable events={[SEC, ELIGIBLE, NONE]} />);

    await user.selectOptions(screen.getByLabelText("PIT status"), "eligible");

    expect(bodyRows()).toHaveLength(1);
    expect(screen.getByText("Pub Co")).toBeInTheDocument();
    expect(screen.getByText(/Showing 1 of 3 loaded rows/)).toBeInTheDocument();
    expect(screen.getByText(/apply to\s+this page only/)).toBeInTheDocument();
  });

  it("filters by text and reports when nothing matches", async () => {
    const user = userEvent.setup();
    render(<EarningsTable events={[SEC, NONE]} />);

    await user.type(screen.getByLabelText("Filter loaded rows"), "zzzz");

    expect(screen.getByText("No loaded rows match this filter.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("sorts by a column via its button and exposes aria-sort", async () => {
    const user = userEvent.setup();
    const low = makeEvent({ id: "low", reported_eps: "-1.0000" });
    const high = makeEvent({ id: "high", reported_eps: "2.0000" });
    render(<EarningsTable events={[low, high]} />);

    await user.click(screen.getByRole("button", { name: /Reported EPS/ }));

    const header = screen.getByRole("columnheader", { name: /Reported EPS/ });
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(within(bodyRows()[0]!).getByText("-1.0000")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Reported EPS/ }));
    expect(header).toHaveAttribute("aria-sort", "descending");
    expect(within(bodyRows()[0]!).getByText("2.0000")).toBeInTheDocument();
  });
});
