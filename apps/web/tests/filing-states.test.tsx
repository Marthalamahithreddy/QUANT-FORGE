import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import FilingDetailPage from "@/app/filings/[accession]/page";
import { ComparativeEpsPanel, CurrentEpsPanel, EpsChangePanel } from "@/components/filings/eps-panels";
import {
  CHANGE_STATUSES,
  COMPARATIVE_STATUSES,
  EPS_STATUSES,
  type ComparativeEps,
  type CurrentEps,
  type EpsChange,
} from "@/lib/api/schema";
import { COMPARATIVE_STATUS_LABEL, EPS_STATUS_LABEL } from "@/lib/filings-view";
import { CHANGE_STATE, COMPARATIVE_STATE, EPS_STATE } from "@/lib/state-map";
import { jsonResponse } from "./fixtures";
import { makeEps, makeFact } from "./filings-fixtures";

vi.mock("next/navigation", () => ({ usePathname: () => "/filings" }));
afterEach(() => vi.unstubAllGlobals());

const fact = (value: string | null) => makeFact({ value }) as unknown as NonNullable<CurrentEps["fact"]>;
const MEANING = "comparative EPS as presented in this filing" as const;

/** Statuses that carry a fact (the decoder enforces the same rule on the wire). */
const factFor = (status: string, nullStatus: string) =>
  status === "AVAILABLE" ? fact("1.5300") : status === nullStatus ? fact(null) : null;

describe("filing refusals map onto the domain-state system without collapsing", () => {
  it.each(EPS_STATUSES)("current EPS %s: its own mark, label, code and the backend's reason", (status) => {
    const current: CurrentEps = { status, reason: `reason for ${status}`, fact: factFor(status, "NULL_VALUE") };
    render(<CurrentEpsPanel current={current} />);
    const frame = screen.getByRole("region", { name: "Current EPS" });

    expect(within(frame).getAllByText(EPS_STATUS_LABEL[status]).length).toBeGreaterThan(0);
    expect(within(frame).getByText(status)).toBeInTheDocument(); // the code, as text
    expect(within(frame).getByText(`reason for ${status}`)).toBeInTheDocument(); // verbatim
    const state = EPS_STATE[status];
    if (state === "VALUE") expect(frame.querySelector("[data-state='VALUE']")).not.toBeNull(); // header badge only
    else expect(frame.querySelector(`[data-state="${state}"]`)).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull(); // a refusal is never an error
  });

  it("keeps every refusal reason distinguishable by label and code, not just by mark", () => {
    const seen = new Set<string>();
    for (const status of EPS_STATUSES) seen.add(`${EPS_STATUS_LABEL[status]}|${status}`);
    expect(seen.size).toBe(EPS_STATUSES.length);
    // Five different statuses share the REFUSED mark; they must still read differently.
    const refused = EPS_STATUSES.filter((s) => EPS_STATE[s] === "REFUSED");
    expect(refused.sort()).toEqual(["COREG_ONLY", "DIMENSIONAL_ONLY", "EXTENSION_ONLY", "UNSUPPORTED_FORM_PERIOD", "WRONG_UNIT"]);
    expect(new Set(refused.map((s) => EPS_STATUS_LABEL[s])).size).toBe(refused.length);
  });

  it("does not render MISSING, NOT_INGESTED, NULL_VALUE and AMBIGUOUS as the same appearance", () => {
    const marks = ["MISSING", "NOT_INGESTED", "NULL_VALUE", "AMBIGUOUS"].map((status) => {
      const { container, unmount } = render(
        <CurrentEpsPanel current={{ status: status as CurrentEps["status"], reason: "r", fact: factFor(status, "NULL_VALUE") }} />,
      );
      const mark = container.querySelector("dd [data-state]")?.getAttribute("data-state");
      unmount();
      return mark;
    });
    expect(marks).toEqual(["MISSING", "MISSING", "NOT_STATED", "AMBIGUOUS"]);
  });

  it.each(COMPARATIVE_STATUSES)("comparative EPS %s: its own mark, label, code and reason", (status) => {
    const comparative: ComparativeEps = {
      status,
      reason: `reason for ${status}`,
      meaning: MEANING,
      fact: factFor(status, "COMPARATIVE_NULL_VALUE"),
    };
    render(<ComparativeEpsPanel comparative={comparative} />);
    const frame = screen.getByRole("region", { name: "Comparative EPS" });

    expect(within(frame).getAllByText(COMPARATIVE_STATUS_LABEL[status]).length).toBeGreaterThan(0);
    expect(within(frame).getByText(status)).toBeInTheDocument();
    expect(within(frame).getByText(`reason for ${status}`)).toBeInTheDocument();
    const state = COMPARATIVE_STATE[status];
    expect(frame.querySelector(`[data-state="${state}"]`)).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each(CHANGE_STATUSES)("EPS change %s", (status) => {
    const change: EpsChange =
      status === "AVAILABLE"
        ? { status, eps_change: "0.5100", transition: "POSITIVE_INCREASED", percentage_change: "0.5", percentage_semantics: "ratio", percentage_unavailable_reason: null }
        : { status, eps_change: null, transition: null, percentage_change: null, percentage_semantics: "ratio", percentage_unavailable_reason: null };
    render(<EpsChangePanel change={change} />);
    const frame = screen.getByRole("region", { name: "EPS change" });
    expect(frame.querySelector(`[data-state="${CHANGE_STATE[status]}"]`)).not.toBeNull();
    expect(within(frame).getByText(status)).toBeInTheDocument();
  });
});

describe("comparative label and descriptive framing", () => {
  it("keeps 'As presented in this filing' on the panel's title line, with its one-sentence meaning", () => {
    render(<ComparativeEpsPanel comparative={makeEps().comparative as ComparativeEps} />);
    const frame = screen.getByRole("region", { name: "Comparative EPS" });
    expect(within(frame).getByText("As presented in this filing")).toBeInTheDocument();
    expect(frame).toHaveTextContent(/not the earlier filing.s original number and not a restatement search/);
  });

  it("never describes the comparative as a generic historical EPS", () => {
    render(<ComparativeEpsPanel comparative={makeEps().comparative as ComparativeEps} />);
    expect(document.body.textContent).not.toMatch(/historical EPS|prior-year EPS database|last year's/i);
  });
});

describe("EPS exactness and the ratio boundary", () => {
  const change = (overrides: Partial<EpsChange>): EpsChange => ({
    status: "AVAILABLE",
    eps_change: "0.5100",
    transition: "POSITIVE_INCREASED",
    percentage_change: "0.5",
    percentage_semantics: "ratio",
    percentage_unavailable_reason: null,
    ...overrides,
  });

  it("shows the API's strings exactly: 50-digit values and trailing zeros survive", () => {
    const long = "0." + "3".repeat(50);
    render(<EpsChangePanel change={change({ eps_change: "123456789012345678901234567890.5000", percentage_change: long })} />);
    const frame = screen.getByRole("region", { name: "EPS change" });
    expect(within(frame).getByText("+123456789012345678901234567890.5000")).toBeInTheDocument();
    expect(frame.querySelector(`[data-exact="${long}"]`)).not.toBeNull();
    expect(within(frame).getByText("+33.33%")).toBeInTheDocument();
  });

  it("a ratio of 0.125 is 12.50%, shifted exactly, and the API value is not mutated", () => {
    render(<EpsChangePanel change={change({ percentage_change: "0.125" })} />);
    const frame = screen.getByRole("region", { name: "EPS change" });
    expect(within(frame).getByText("+12.50%")).toBeInTheDocument();
    expect(frame.querySelector('[data-exact="0.125"]')).not.toBeNull();
  });

  it("a negative ratio keeps its sign and the signed change is not recomputed", () => {
    render(<EpsChangePanel change={change({ eps_change: "-1.5000", transition: "PROFIT_TO_LOSS", percentage_change: "-1.5" })} />);
    const frame = screen.getByRole("region", { name: "EPS change" });
    expect(within(frame).getByText("-1.5000")).toBeInTheDocument();
    expect(within(frame).getByText("-150.00%")).toBeInTheDocument();
  });

  it.each(["ZERO_COMPARATIVE", "NEGATIVE_COMPARATIVE"] as const)(
    "an undefined percentage (%s) is UNAVAILABLE with its reason and is never 0%%",
    (reason) => {
      render(<EpsChangePanel change={change({ percentage_change: null, percentage_unavailable_reason: reason })} />);
      const frame = screen.getByRole("region", { name: "EPS change" });
      expect(frame.querySelector('[data-state="UNAVAILABLE"]')).not.toBeNull();
      expect(frame).toHaveTextContent(/not defined for this comparative base/);
      expect(frame.textContent).not.toMatch(/\b0(\.0+)?%/);
    },
  );
});

describe("selectors stay explicit after a refusal", () => {
  const SEL = { period_class: "QUARTER", eps_kind: "DILUTED" };
  const props = (search: Record<string, string>) =>
    ({ params: Promise.resolve({ accession: "0000320193-24-000010" }), searchParams: Promise.resolve(search) }) as never;

  it("shows a refused DILUTED EPS under the SAME selectors and does not switch to another kind", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(makeEps({ current: { status: "MISSING", reason: "No diluted EPS fact is stored.", fact: null }, comparative: { status: "CURRENT_UNAVAILABLE", reason: "unavailable", meaning: MEANING, fact: null }, change: { status: "INPUT_UNAVAILABLE", eps_change: null, transition: null, percentage_change: null, percentage_semantics: "ratio", percentage_unavailable_reason: null } })))));
    render(await FilingDetailPage(props(SEL)));

    expect(screen.getByLabelText("EPS kind")).toHaveValue("DILUTED");
    expect(screen.getByLabelText("Period class")).toHaveValue("QUARTER");
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    expect(String(vi.mocked(fetch).mock.calls[0]![0])).toContain("eps_kind=DILUTED");
    expect(screen.getByRole("region", { name: "Current EPS" })).toHaveTextContent("No diluted EPS fact is stored.");
  });

  it("marks an unsupported selector invalid and points it at the error that explains it", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(await FilingDetailPage(props({ period_class: "YTD", eps_kind: "DILUTED" })));

    const alert = screen.getByRole("alert");
    const period = screen.getByLabelText("Period class");
    expect(period).toHaveAttribute("aria-invalid", "true");
    expect(period).toHaveAttribute("aria-describedby", alert.id);
    expect(screen.getByLabelText("EPS kind")).not.toHaveAttribute("aria-invalid");
    expect(vi.mocked(fetch)).not.toHaveBeenCalled(); // no request for an unsupported selector
  });

  it("shows breadcrumbs, one h1 and h2 panels, and a one-request page", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(makeEps()))));
    render(await FilingDetailPage(props(SEL)));

    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Filings" })).toHaveAttribute("href", "/filings");
    expect(within(crumbs).getByText("0000320193-24-000010")).toHaveAttribute("aria-current", "page");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Current EPS", "Comparative EPS", "EPS change"]);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });
});
