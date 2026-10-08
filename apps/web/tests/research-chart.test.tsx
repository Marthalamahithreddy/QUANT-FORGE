/* eslint-disable @typescript-eslint/no-explicit-any -- tests mutate deep, deliberately untyped payloads to model drift */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HorizonReturnsChart } from "@/components/research/horizon-returns-chart";
import { ReturnsTable } from "@/components/research/returns-table";
import { SessionLadder } from "@/components/research/session-ladder";
import type { Study } from "@/lib/api/schema";
import { formatRatioAsPercent } from "@/lib/format";
import { captured, clone } from "./research-fixtures";

type Json = Record<string, any>;
const study = (name: string, mode: "descriptive" | "pit" = "descriptive"): Study => {
  const response = captured(name).response;
  return (mode === "descriptive" ? response.descriptive.study : response.point_in_time.study) as Study;
};
const plot = (container: HTMLElement) => container.querySelector("svg[role=img]")!;
const chart = (s: Study) => render(<HorizonReturnsChart study={s} title="Descriptive return by horizon" />);

describe("the chart is a supplement to the table, from the same returned figures", () => {
  it("has one focusable group per horizon whose accessible name carries the exact figures", () => {
    chart(study("benchmark_selected"));
    const groups = screen.getAllByRole("group");
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveAttribute("tabindex", "0");
    expect(groups[0]).toHaveAccessibleName("+1 sessions: stock return +10.00%, benchmark +2.50%, abnormal return +7.50%");
    expect(groups[1]).toHaveAccessibleName("+2 sessions: stock return +20.00%, benchmark +5.00%, abnormal return +15.00%");
    expect(groups[0]!.querySelector("title")!.textContent).toBe(groups[0]!.getAttribute("aria-label"));
  });

  it("chart figures equal the table figures, horizon by horizon, from the same strings", () => {
    const s = study("benchmark_selected");
    const { container } = render(
      <>
        <HorizonReturnsChart study={s} title="Descriptive return by horizon" />
        <ReturnsTable study={s} caption="Descriptive returns by horizon" />
      </>,
    );
    const table = within(container).getByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    const groups = screen.getAllByRole("group");
    s.stock_returns.forward_returns.forEach((entry, index) => {
      const row = rows[index]!;
      expect(row.textContent).toContain(formatRatioAsPercent(entry.return_value));
      expect(row.textContent).toContain(formatRatioAsPercent(s.benchmark.returns[index]!.benchmark_return));
      expect(row.textContent).toContain(formatRatioAsPercent(s.abnormal_returns[index]!.abnormal_return));
      expect(groups[index]!.getAttribute("aria-label")).toContain(formatRatioAsPercent(entry.return_value));
      // the label printed beside the stock mark is the same exact string
      expect(within(groups[index] as HTMLElement).getByText(formatRatioAsPercent(entry.return_value))).toBeInTheDocument();
    });
  });

  it("names itself, describes what it shows and states the units and the basis", () => {
    const { container } = chart(study("benchmark_selected"));
    const svg = container.querySelector("svg[role=img]")!;
    expect(svg).toHaveAccessibleName(/Descriptive return by horizon/);
    expect(svg).toHaveAccessibleDescription(/Stock return at 2 horizons, in sessions after the anchor session, with the explicitly selected benchmark/);
    expect(svg).toHaveAccessibleDescription(/Split-adjusted price returns, dividends excluded/);
    expect(svg.textContent).toContain("Return (%)");
    expect(svg.textContent).toContain("Sessions after the anchor session");
  });

  it("lives in a labelled, focusable scroll region so it stays legible at narrow widths", () => {
    chart(study("benchmark_selected"));
    const region = screen.getByRole("region", { name: "Descriptive return by horizon, scrollable" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.className).toMatch(/overflow-x-auto/);
    expect(region.className).toMatch(/\brelative\b/);
    expect(region.querySelector("svg")!.getAttribute("class")).toMatch(/max-w-none/);
  });
});

describe("what is drawn and what is not", () => {
  it("draws a filled circle per stock return, a hollow diamond per benchmark return and the backend's gap between them", () => {
    const { container } = chart(study("benchmark_selected"));
    expect(plot(container).querySelectorAll("circle.fill-chart-stock")).toHaveLength(2);
    const diamonds = plot(container).querySelectorAll("path");
    expect(diamonds).toHaveLength(2);
    for (const diamond of diamonds) expect(diamond.getAttribute("fill")).toBe("none");
    expect(plot(container).querySelectorAll("line.stroke-chart-abnormal")).toHaveLength(2);
  });

  it("never joins horizons: no polyline, no path across slots, no interpolation", () => {
    const { container } = chart(study("benchmark_selected"));
    expect(container.querySelectorAll("polyline, polygon")).toHaveLength(0);
    const connectors = [...plot(container).querySelectorAll("line.stroke-chart-abnormal")];
    for (const line of connectors) expect(line.getAttribute("x1")).toBe(line.getAttribute("x2")); // vertical: within one slot only
  });

  it("draws no benchmark or abnormal marks when no benchmark was selected", () => {
    const { container } = chart(study("single_horizon"));
    expect(plot(container).querySelectorAll("circle.fill-chart-stock")).toHaveLength(1);
    expect(plot(container).querySelectorAll("path")).toHaveLength(0);
    expect(plot(container).querySelectorAll("line.stroke-chart-abnormal")).toHaveLength(0);
    expect(container.textContent).toContain("No benchmark selected: stock returns only.");
    expect(container.querySelector("svg[role=img]")!.getAttribute("aria-label") ?? "").not.toMatch(/benchmark/);
  });

  it("a horizon that could not be computed has NO mark: an empty slot names its state instead", () => {
    const s = study("default_no_benchmark"); // +1 available; +5 and +20 are proven sessions with no stored price
    const { container } = chart(s);
    expect(plot(container).querySelectorAll("circle.fill-chart-stock")).toHaveLength(1);
    const groups = screen.getAllByRole("group");
    expect(groups).toHaveLength(3);
    expect(groups[1]).toHaveAccessibleName("+5 sessions: stock return price not stored for target session");
    expect(groups[1]!.querySelector("circle.fill-chart-stock")).toBeNull(); // (the state glyph is a hollow ring, not a data mark)
    expect(groups[1]!.textContent).toContain("Price not stored for target session");
    expect(groups[1]!.querySelector("svg[data-glyph]")).not.toBeNull(); // the state's glyph, not a zero
    expect(groups[1]!.textContent).not.toMatch(/0\.00%|\+0%/);
  });

  it("no horizon has a return (unpriced and unprovable targets only): no chart, no zero, no coordinate system", () => {
    const { container } = chart(study("horizons_beyond_calendar"));
    expect(container.querySelector("svg")).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("when only the benchmark is missing the stock mark stays and the slot says why", () => {
    const { container } = chart(study("benchmark_without_data"));
    expect(plot(container).querySelectorAll("circle.fill-chart-stock")).toHaveLength(2);
    expect(plot(container).querySelectorAll("path")).toHaveLength(0);
    expect(screen.getAllByRole("group")[0]).toHaveAccessibleName("+1 sessions: stock return +10.00%, benchmark missing anchor price");
    expect(container.textContent).toContain("Benchmark: Missing anchor price");
  });

  it("renders nothing for a study with no stock return at all: no empty coordinate system", () => {
    const { container } = chart(study("unknown_timing"));
    expect(container.querySelector("svg")).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("labels the horizons as sessions after the anchor and carries no event at a clock position", () => {
    const { container } = chart(study("benchmark_selected"));
    expect(container.textContent).toMatch(/\+1/);
    expect(container.textContent).toMatch(/\+2/);
    expect(container.textContent).not.toMatch(/\d{2}:\d{2}|UTC|Event|before open|after close/i);
    expect(container.querySelector("svg")!.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}/);
  });

  it("draws a zero line and symmetric ticks around it", () => {
    const { container } = chart(study("benchmark_selected"));
    expect(container.querySelector("line.stroke-chart-zero")).not.toBeNull();
    const ticks = [...container.querySelectorAll("text.fill-text-3")].map((t) => t.textContent).filter((t) => /%$/.test(t ?? ""));
    expect(ticks).toContain("0%");
    expect(ticks.filter((t) => t!.startsWith("-")).map((t) => t!.slice(1)).sort()).toEqual(ticks.filter((t) => /^\d/.test(t!) && t !== "0%").sort());
  });

  it("scales to the data: a tiny return still draws a legible mark", () => {
    const tiny = clone(study("single_horizon")) as unknown as Json;
    tiny.stock_returns.forward_returns[0].return_value = "0.00001";
    const { container } = chart(tiny as Study);
    const circle = container.querySelector("circle")!;
    expect(Number(circle.getAttribute("cy"))).not.toBeNaN();
    expect(container.querySelector("g[role=group]")).toHaveAccessibleName("+1 sessions: stock return 0.00% (not zero)");
  });

  it("uses theme tokens only: no literal colour in the SVG", () => {
    const { container } = chart(study("benchmark_selected"));
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(/);
    expect(container.innerHTML).toMatch(/chart-stock|chart-bench|chart-abnormal|chart-zero|chart-grid/);
  });
});

describe("returns table", () => {
  it("lists every horizon with the backend's statuses, exact closes and no benchmark columns when none was selected", () => {
    render(<ReturnsTable study={study("default_no_benchmark")} caption="Descriptive returns by horizon" />);
    const table = screen.getByRole("table", { name: "Descriptive returns by horizon" });
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByText("+1 sessions")).toBeInTheDocument();
    expect(within(table).getAllByText("MISSING_TARGET_PRICE")).toHaveLength(2);
    expect(within(table).getByText("110.000000")).toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: /Benchmark target close/ })).toBeNull();
  });

  it("tells the three unavailable statuses apart: unpriced anchor never appears here, unpriced target and unprovable target do", () => {
    render(<ReturnsTable study={study("horizons_beyond_calendar")} caption="Descriptive returns by horizon" />);
    const table = screen.getByRole("table", { name: "Descriptive returns by horizon" });
    expect(within(table).getAllByText("MISSING_TARGET_PRICE")).toHaveLength(2);
    expect(within(table).getAllByText("INSUFFICIENT_FORWARD_SESSIONS")).toHaveLength(1);
  });

  it("a stock-only table (the event record's preview) leaves out the benchmark and abnormal columns", () => {
    render(<ReturnsTable study={study("default_no_benchmark")} caption="Stock returns by horizon" stockOnly />);
    const names = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(names).not.toContain("Benchmark return");
    expect(names).not.toContain("Abnormal return (stock minus benchmark)");
    expect(names).toContain("Stock return");
  });

  it("hides secondary columns by priority instead of stacking, and scrolls in a labelled region", () => {
    render(<ReturnsTable study={study("benchmark_selected")} caption="Descriptive returns by horizon" />);
    const head = (name: string) => screen.getByRole("columnheader", { name }).className;
    expect(head("Stock return")).not.toMatch(/\bhidden\b/);
    expect(head("Target session")).toMatch(/hidden md:table-cell/);
    expect(head("Stock target close")).toMatch(/hidden xl:table-cell/);
    expect(screen.getByRole("region", { name: "Descriptive returns by horizon, scrollable" })).toHaveAttribute("tabindex", "0");
  });

  it("an abnormal return the backend marked unavailable is UNAVAILABLE, and NOT_REQUESTED without a benchmark", () => {
    render(<ReturnsTable study={study("unknown_timing")} caption="t" />);
    const marks = [...document.querySelectorAll("tbody [data-state]")].map((el) => el.getAttribute("data-state"));
    expect(marks).toContain("UNAVAILABLE");
    expect(marks).not.toContain("NOT_REQUESTED");
  });
});

describe("session ladder", () => {
  const modified = (name: string, edit: (s: Json) => void): Study => {
    const s = clone(study(name)) as unknown as Json;
    edit(s);
    return s as Study;
  };

  it("before-open: the event marker sits before the anchor slot and is labelled with the timing, never a clock time", () => {
    render(<SessionLadder study={study("benchmark_selected")} />);
    const items = within(screen.getByRole("list", { name: "Stored trading sessions" })).getAllByRole("listitem");
    expect(items[0]).toHaveAttribute("data-kind", "event-marker");
    expect(items[0]).toHaveTextContent("Before open (as stated by the source)");
    expect(items[1]).toHaveTextContent("Anchor · first post-event session2026-03-02");
    expect(items[2]).toHaveTextContent("+1 sessions2026-03-03");
    expect(items[3]).toHaveTextContent("+2 sessions2026-03-04");
    expect(document.body.textContent).not.toMatch(/\d{2}:\d{2}/);
  });

  it("after-close: the event session comes first, then the marker, then the next session as the anchor", () => {
    const s = modified("benchmark_selected", (x) => {
      x.alignment.timing = "AFTER_MARKET";
      x.alignment.session_alignment.event_session = "2026-03-02";
      x.alignment.session_alignment.first_post_event_session = "2026-03-03";
    });
    render(<SessionLadder study={s} />);
    const items = within(screen.getByRole("list", { name: "Stored trading sessions" })).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Event session2026-03-02");
    expect(items[1]).toHaveAttribute("data-kind", "event-marker");
    expect(items[1]).toHaveTextContent("After close (as stated by the source)");
    expect(items[2]).toHaveTextContent("Anchor · first post-event session2026-03-03");
  });

  it("a proven target session whose price is not stored shows its date AND says the price is missing", () => {
    render(<SessionLadder study={study("default_no_benchmark")} />);
    const items = within(screen.getByRole("list", { name: "Stored trading sessions" })).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(items[2]).toHaveTextContent("+1 sessions2026-03-03");
    expect(items[2]).not.toHaveTextContent("Price not stored");
    expect(items[3]).toHaveTextContent("+5 sessions2026-03-09Price not stored for target session"); // the proven session, never moved
    expect(items[4]).toHaveTextContent("+20 sessions2026-03-30Price not stored for target session");
  });

  it("manufactures no date: a horizon the calendar cannot prove shows its state and no date", () => {
    render(<SessionLadder study={study("horizons_beyond_calendar")} />);
    const items = within(screen.getByRole("list", { name: "Stored trading sessions" })).getAllByRole("listitem");
    expect(items).toHaveLength(5); // marker, anchor, +1 and +2 (proven, unpriced), +5 (not provable)
    expect(items[3]).toHaveTextContent("+2 sessions");
    expect(items[3]).toHaveTextContent("Price not stored for target session");
    expect(items[4]).toHaveTextContent("+5 sessions");
    expect(items[4]!.querySelector('[data-state="MISSING"]')).not.toBeNull();
    expect(items[4]!.querySelector(".num")).toBeNull(); // no session date is shown for it (only the backend's reason text)
    expect(items[4]).toHaveTextContent("Beyond the calendar's proven horizon");
  });

  it("a refused alignment replaces the ladder with the backend's reason", () => {
    render(<SessionLadder study={study("unknown_timing")} />);
    expect(screen.queryByRole("list", { name: "Stored trading sessions" })).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("source declared no timing; before- vs after-market cannot be inferred");
    expect(screen.getByRole("status")).toHaveTextContent("UNKNOWN_TIMING");
  });

  it("scrolls sideways in a labelled, focusable, positioned region (keyboard users can scroll it; hidden state names cannot widen the page)", () => {
    render(<SessionLadder study={study("default_no_benchmark")} />);
    const region = screen.getByRole("region", { name: "Session ladder, scrollable" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.className).toMatch(/overflow-x-auto/);
    expect(region.className).toMatch(/\brelative\b/);
    expect(within(region).getByRole("list", { name: "Stored trading sessions" })).toBeInTheDocument();
  });

  it("the on-chart value label is the exact percent only; the 'not zero' flag lives in the accessible name and the table", () => {
    const tiny = clone(study("single_horizon")) as unknown as Json;
    tiny.stock_returns.forward_returns[0].return_value = "0.00001";
    const { container } = chart(tiny as Study);
    const group = container.querySelector("g[role=group]")!;
    expect(group.querySelector("text.num")!.textContent).toBe("0.00%");
    expect(group).toHaveAccessibleName(/\(not zero\)/);
  });

  it("states the ladder is not to scale and is not a clock", () => {
    render(<SessionLadder study={study("benchmark_selected")} />);
    expect(screen.getByText(/not to scale/)).toBeInTheDocument();
    expect(screen.getByText(/never at a clock time/)).toBeInTheDocument();
  });
});
