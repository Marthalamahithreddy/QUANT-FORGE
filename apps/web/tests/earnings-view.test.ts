import { describe, expect, it } from "vitest";

import { matchesFilters, sortEvents, tickersAtEvent } from "@/lib/earnings-view";
import { makeEvent } from "./fixtures";

const a = makeEvent({ id: "a", reported_eps: "0.30", event_date: "2024-01-01" });
const b = makeEvent({ id: "b", reported_eps: "0.3000000001", event_date: "2024-02-01" });
const c = makeEvent({ id: "c", reported_eps: null, event_date: "2024-03-01" });
const d = makeEvent({ id: "d", reported_eps: "-1", event_date: "2024-04-01" });

describe("sortEvents", () => {
  it("sorts decimals exactly and keeps missing values last in both directions", () => {
    const asc = sortEvents([a, b, c, d], "reported_eps", "asc").map((e) => e.id);
    const desc = sortEvents([a, b, c, d], "reported_eps", "desc").map((e) => e.id);

    expect(asc).toEqual(["d", "a", "b", "c"]);
    expect(desc).toEqual(["b", "a", "d", "c"]);
  });

  it("sorts by date and does not mutate the input", () => {
    const input = [b, a];
    expect(sortEvents(input, "event_date", "asc").map((e) => e.id)).toEqual(["a", "b"]);
    expect(input.map((e) => e.id)).toEqual(["b", "a"]);
  });

  it("puts events without observed_at last when sorting by availability", () => {
    const seen = makeEvent({
      id: "seen",
      availability: {
        observed_at: "2024-03-15T11:00:00Z",
        observed_at_basis: "SOURCE_PUBLICATION",
        pit_eligibility: "ELIGIBLE",
        pit_reason: "ok",
      },
    });
    expect(sortEvents([a, seen], "observed_at", "desc").map((e) => e.id)).toEqual(["seen", "a"]);
    expect(sortEvents([a, seen], "observed_at", "asc").map((e) => e.id)).toEqual(["seen", "a"]);
  });
});

describe("tickersAtEvent", () => {
  it("uses only listings the backend marked valid on the event date", () => {
    const event = makeEvent({
      listings: [
        { ...makeEvent().listings[0]!, ticker: "OLD", valid_on_event_date: false },
        { ...makeEvent().listings[0]!, ticker: "NEW", valid_on_event_date: true },
      ],
    });
    expect(tickersAtEvent(event)).toEqual(["NEW"]);
  });
});

describe("matchesFilters", () => {
  const eligible = makeEvent({
    availability: {
      observed_at: "2024-03-15T11:00:00Z",
      observed_at_basis: "SOURCE_PUBLICATION",
      pit_eligibility: "ELIGIBLE",
      pit_reason: "ok",
    },
  });

  it("filters on the backend's classification, not on observed_at", () => {
    const sec = makeEvent({
      availability: {
        observed_at: "2024-03-15T11:00:00Z",
        observed_at_basis: "SEC_FILING_ACCEPTANCE",
        pit_eligibility: "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS",
        pit_reason: "no",
      },
    });
    expect(matchesFilters(sec, "", "eligible")).toBe(false);
    expect(matchesFilters(sec, "", "ineligible")).toBe(true);
    expect(matchesFilters(eligible, "", "eligible")).toBe(true);
    expect(matchesFilters(eligible, "", "ineligible")).toBe(false);
  });

  it("does not match a ticker the table does not show for that event", () => {
    const event = makeEvent({
      listings: [{ ...makeEvent().listings[0]!, ticker: "CURRENT", valid_on_event_date: false }],
    });
    expect(matchesFilters(event, "current", "all")).toBe(false);
  });

  it("matches company, ticker and source text case-insensitively", () => {
    expect(matchesFilters(a, "acme", "all")).toBe(true);
    expect(matchesFilters(a, "TEST-VEND", "all")).toBe(true);
    expect(matchesFilters(a, "zzz", "all")).toBe(false);
  });
});
