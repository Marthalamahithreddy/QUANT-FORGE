import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ApiFailureState } from "@/components/ui/api-failure";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { SelectField, TextField } from "@/components/ui/fields";
import { FilterBar } from "@/components/ui/filter-bar";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import { Pagination } from "@/components/ui/pagination";
import { TableSkeleton } from "@/components/ui/table-skeleton";
import type { ApiFailure } from "@/lib/api/result";

interface Row {
  id: string;
  name: string;
  amount: string;
  extra: string;
  later: string;
}
const ROWS: Row[] = [
  { id: "1", name: "Alpha", amount: "1.5300", extra: "x1", later: "y1" },
  { id: "2", name: "Beta", amount: "-0.0100", extra: "x2", later: "y2" },
];
const COLUMNS: DataTableColumn<Row>[] = [
  { id: "name", header: "Name", cell: (row) => <a href={`/r/${row.id}`}>{row.name}</a> },
  { id: "amount", header: "Amount", align: "end", cell: (row) => row.amount },
  { id: "extra", header: "Extra", priority: "P2", cell: (row) => row.extra },
  { id: "later", header: "Later", priority: "P3", cell: (row) => row.later },
];

describe("DataTable", () => {
  const table = () => render(<DataTable caption="Things" columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} />);

  it("is a real table with a caption naming it, and column headers scoped to columns", () => {
    table();
    const el = screen.getByRole("table", { name: "Things" });
    expect(within(el).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Name", "Amount", "Extra", "Later"]);
    for (const header of within(el).getAllByRole("columnheader")) expect(header).toHaveAttribute("scope", "col");
  });

  it("makes the first column the row header (the identity), and only that column", () => {
    table();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual(["Alpha", "Beta"]);
    expect(within(rows[0]!).getAllByRole("cell")).toHaveLength(3);
  });

  it("puts the link in the first cell and makes no row a click target", () => {
    table();
    const row = screen.getAllByRole("row")[1]!;
    expect(within(within(row).getByRole("rowheader")).getByRole("link", { name: "Alpha" })).toHaveAttribute("href", "/r/1");
    expect(row).not.toHaveAttribute("onclick");
    expect(row).not.toHaveAttribute("tabindex");
    expect(row).not.toHaveAttribute("role", "link");
  });

  it("right-aligns numeric columns with tabular figures and leaves text columns left", () => {
    table();
    expect(screen.getByRole("columnheader", { name: "Amount" })).toHaveClass("text-right");
    expect(screen.getByText("1.5300").closest("td")).toHaveClass("text-right", "num");
    expect(screen.getByText("x1").closest("td")).not.toHaveClass("text-right");
  });

  it("scrolls inside a focusable, labelled region and keeps the header and identity column sticky", () => {
    table();
    const region = screen.getByRole("region", { name: "Things, scrollable" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.className).toContain("overflow-auto");
    // The region must be positioned, or absolutely positioned sr-only text widens the page.
    expect(region.className).toMatch(/\brelative\b/);
    expect(screen.getByRole("columnheader", { name: "Name" }).className).toMatch(/sticky.*left-0|left-0.*sticky/);
    expect(screen.getByRole("columnheader", { name: "Amount" }).className).toContain("sticky");
    expect(screen.getAllByRole("rowheader")[0]!.className).toMatch(/sticky/);
    expect(screen.getAllByRole("rowheader")[0]!.className).toContain("bg-surface");
  });

  it("is reachable and operable by keyboard: the region and the identity links take focus", async () => {
    const user = userEvent.setup();
    table();
    await user.tab();
    expect(screen.getByRole("region", { name: "Things, scrollable" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: "Alpha" })).toHaveFocus();
  });

  it("hides lower-priority columns by width classes, and says how many are hidden", () => {
    table();
    expect(screen.getByRole("columnheader", { name: "Name" }).className).not.toMatch(/\bhidden\b/);
    expect(screen.getByRole("columnheader", { name: "Extra" }).className).toContain("hidden md:table-cell");
    expect(screen.getByRole("columnheader", { name: "Later" }).className).toContain("hidden xl:table-cell");
    expect(screen.getByTestId("columns-note-narrow")).toHaveTextContent("2 more columns at wider widths");
    expect(screen.getByTestId("columns-note-medium")).toHaveTextContent("1 more column at wider widths");
  });

  it("states no hidden-column note when every column is P1", () => {
    render(<DataTable caption="Flat" columns={COLUMNS.slice(0, 2)} rows={ROWS} rowKey={(row) => row.id} />);
    expect(screen.queryByTestId("columns-note-narrow")).toBeNull();
  });

  it("renders the empty element instead of a table when there are no rows (never fake rows)", () => {
    render(<DataTable caption="Things" columns={COLUMNS} rows={[]} rowKey={(row) => row.id} empty={<p role="status">Nothing here</p>} />);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Nothing here");
  });

  it("does not sort, filter or mark any column as sorted", () => {
    table();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(document.querySelectorAll("[aria-sort]")).toHaveLength(0);
  });

  it("keeps duplicate-looking rows distinct by the caller's key (a joint accession is two rows)", () => {
    const rows = [ROWS[0]!, { ...ROWS[0]!, id: "1b" }];
    render(<DataTable caption="Dup" columns={COLUMNS} rows={rows} rowKey={(row) => row.id} />);
    expect(screen.getAllByRole("row")).toHaveLength(3);
  });

  it("renders the largest page the API allows (200 rows x 12 columns) without virtualisation", () => {
    const cols: DataTableColumn<number>[] = Array.from({ length: 12 }, (_, c) => ({ id: `c${c}`, header: `H${c}`, cell: (n) => `${n}-${c}` }));
    render(<DataTable caption="Large" columns={cols} rows={Array.from({ length: 200 }, (_, n) => n)} rowKey={String} />);
    expect(screen.getAllByRole("row")).toHaveLength(201);
  });
});

describe("Pagination", () => {
  const href = (page: number) => (page === 1 ? "/list" : `/list?page=${page}`);
  const view = (props: Partial<Parameters<typeof Pagination>[0]> = {}) =>
    render(<Pagination total={1234} limit={50} offset={50} maxPage={10_000} hrefFor={href} noun="rows" {...props} />);

  it("is a labelled navigation stating the position in words", () => {
    view();
    const nav = screen.getByRole("navigation", { name: "Pagination" });
    expect(within(nav).getByTestId("pagination-position")).toHaveTextContent("Rows 51–100 of 1,234 rows");
    expect(within(nav).getByText("Page 2 of 25")).toBeInTheDocument();
  });

  it("links to the canonical previous and next pages (back/forward just work)", () => {
    view();
    expect(screen.getByRole("link", { name: "Previous page" })).toHaveAttribute("href", "/list");
    expect(screen.getByRole("link", { name: "Next page" })).toHaveAttribute("href", "/list?page=3");
  });

  it("disables the first boundary as a labelled non-link", () => {
    view({ offset: 0 });
    expect(screen.queryByRole("link", { name: "Previous page" })).toBeNull();
    expect(screen.getByText("Previous page")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("link", { name: "Next page" })).toBeInTheDocument();
  });

  it("disables the last boundary, with a partial final page", () => {
    view({ offset: 1200 });
    expect(screen.getByTestId("pagination-position")).toHaveTextContent("Rows 1,201–1,234 of 1,234");
    expect(screen.queryByRole("link", { name: "Next page" })).toBeNull();
    expect(screen.getByText("Next page")).toHaveAttribute("aria-disabled", "true");
  });

  it("never offers a page past the bound the URL accepts", () => {
    view({ total: 1_000_000, offset: 49_950, maxPage: 1000 });
    expect(screen.queryByRole("link", { name: "Next page" })).toBeNull();
  });

  it("a single page has neither link", () => {
    view({ total: 3, offset: 0 });
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText("Page 1 of 1")).toBeInTheDocument();
  });

  it("uses touch-sized targets below the desktop breakpoint", () => {
    view();
    expect(screen.getByRole("link", { name: "Next page" }).className).toContain("max-lg:min-h-11");
  });
});

describe("FilterBar and fields", () => {
  const bar = (clearHref?: string, invalid = false) =>
    render(
      <FilterBar action="/list" label="Filter things" clearHref={clearHref}>
        <TextField id="ticker" label="Ticker" defaultValue="ACME" maxLength={32} invalid={invalid} describedBy="why" />
        <SelectField id="form" label="Form" defaultValue="10-Q">
          <option value="">Any</option>
          <option value="10-Q">10-Q</option>
        </SelectField>
      </FilterBar>,
    );

  it("is a GET form to the list's own path, named for assistive technology", () => {
    bar();
    const form = screen.getByRole("form", { name: "Filter things" });
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", "/list");
  });

  it("labels every control and shows the current value", () => {
    bar();
    expect(screen.getByLabelText("Ticker")).toHaveValue("ACME");
    expect(screen.getByLabelText("Form")).toHaveValue("10-Q");
    expect(screen.getByRole("button", { name: "Apply" })).toHaveAttribute("type", "submit");
  });

  it("shows Clear only when a filter is active", () => {
    const { unmount } = bar();
    expect(screen.queryByRole("link", { name: "Clear" })).toBeNull();
    unmount();
    bar("/list");
    expect(screen.getByRole("link", { name: "Clear" })).toHaveAttribute("href", "/list");
  });

  it("associates an invalid control with its explanation, and only when invalid", () => {
    const { unmount } = bar(undefined, true);
    expect(screen.getByLabelText("Ticker")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Ticker")).toHaveAttribute("aria-describedby", "why");
    unmount();
    bar();
    expect(screen.getByLabelText("Ticker")).not.toHaveAttribute("aria-invalid");
    expect(screen.getByLabelText("Ticker")).not.toHaveAttribute("aria-describedby");
  });

  it("controls grow to 44px below desktop and use the 3:1 control border", () => {
    bar();
    expect(screen.getByLabelText("Ticker").className).toContain("border-border-control");
    expect(screen.getByLabelText("Ticker").className).toContain("max-lg:min-h-11");
    expect(screen.getByRole("button", { name: "Apply" }).className).toContain("max-lg:min-h-11");
  });

  it("has no client behaviour: no handlers, no debounce, the form just submits", () => {
    bar();
    expect(screen.getByRole("form").querySelectorAll("[onchange],[oninput]")).toHaveLength(0);
  });
});

describe("ApiFailureState: a failed read, never a domain refusal", () => {
  const FAILURES: ApiFailure[] = [
    { kind: "not_found" },
    { kind: "conflict" },
    { kind: "rejected", issues: [{ where: "query.ticker", message: "too long" }] },
    { kind: "unavailable" },
    { kind: "server_error", status: 500 },
    { kind: "unreachable", timedOut: false },
    { kind: "unreachable", timedOut: true },
    { kind: "contract", path: "$.items[0].form", expected: "a known filing form" },
  ];

  it.each(FAILURES)("%j is announced as an alert with a title and a safe description", (failure) => {
    render(<ApiFailureState failure={failure} subject="the widgets" retryHref="/w" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The QuantForge API did not return the widgets.");
    expect(alert.textContent).not.toMatch(/https?:|localhost|stack|SELECT/i);
    expect(alert.querySelector('[data-glyph="triangle"]')).not.toBeNull();
  });

  it("offers Try again where retrying can help, and not for a rejection, a contract error or a 404", () => {
    const retry = (failure: ApiFailure) => {
      const { unmount } = render(<ApiFailureState failure={failure} subject="x" retryHref="/w" />);
      const has = screen.queryByRole("link", { name: "Try again" }) !== null;
      unmount();
      return has;
    };
    expect(retry({ kind: "unavailable" })).toBe(true);
    expect(retry({ kind: "unreachable", timedOut: false })).toBe(true);
    expect(retry({ kind: "server_error", status: 500 })).toBe(true);
    expect(retry({ kind: "rejected", issues: [] })).toBe(false);
    expect(retry({ kind: "contract", path: "$", expected: "x" })).toBe(false);
    expect(retry({ kind: "not_found" })).toBe(false);
  });

  it("uses the page's own 404 title when it has one", () => {
    render(<ApiFailureState failure={{ kind: "not_found" }} subject="the event" notFoundTitle="No stored event has this identifier" />);
    expect(screen.getByRole("alert")).toHaveTextContent("No stored event has this identifier");
  });

  it("explains a 503 as a service problem, not an empty result", () => {
    render(<ApiFailureState failure={{ kind: "unavailable" }} subject="x" />);
    expect(screen.getByRole("alert")).toHaveTextContent("not an empty result");
  });

  it("shows the contract path only outside production, and never the received value", () => {
    render(<ApiFailureState failure={{ kind: "contract", path: "$.reported_eps", expected: "an exact decimal string" }} subject="x" />);
    expect(screen.getByRole("alert")).toHaveTextContent("$.reported_eps");
  });
});

describe("KeyValueList and TableSkeleton", () => {
  it("renders label/value rows as a definition list", () => {
    render(
      <KeyValueList>
        <KeyValue label="CIK">0000320193</KeyValue>
      </KeyValueList>,
    );
    expect(screen.getByText("CIK").tagName).toBe("DT");
    expect(screen.getByText("0000320193").tagName).toBe("DD");
    expect(screen.getByText("0000320193")).toHaveClass("num");
  });

  it("announces what is loading, shows no spinner, and keeps the blocks decorative", () => {
    const { container } = render(<TableSkeleton label="Loading earnings events" />);
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-busy", "true");
    expect(region).toHaveTextContent("Loading earnings events");
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(10);
    expect(container.querySelector("svg")).toBeNull();
    expect(container.querySelectorAll(".qf-skeleton").length).toBeGreaterThan(0);
  });
});
