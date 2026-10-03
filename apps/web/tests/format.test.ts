import { describe, expect, it } from "vitest";

import {
  MISSING,
  compareDecimalStrings,
  formatDate,
  formatDecimal,
  formatRatioAsPercent,
  formatSignedDecimal,
  formatUtcInstant,
} from "@/lib/format";

describe("formatDecimal", () => {
  it("passes a decimal string through untouched, keeping trailing zeros", () => {
    expect(formatDecimal("0.3000")).toBe("0.3000");
    expect(formatDecimal("-12.5000")).toBe("-12.5000");
  });

  it("keeps digits a float could not represent", () => {
    expect(formatDecimal("12345678901234567890.1234")).toBe("12345678901234567890.1234");
    expect(formatDecimal("0.1000000000000000055511151231257827")).toBe(
      "0.1000000000000000055511151231257827",
    );
  });

  it("shows an explicit dash for null or non-decimal text", () => {
    expect(formatDecimal(null)).toBe(MISSING);
    expect(formatDecimal("NaN")).toBe(MISSING);
    expect(formatDecimal("1e5")).toBe(MISSING);
  });
});

describe("formatSignedDecimal", () => {
  it("adds a plus to positives only", () => {
    expect(formatSignedDecimal("0.2000")).toBe("+0.2000");
    expect(formatSignedDecimal("-0.2000")).toBe("-0.2000");
    expect(formatSignedDecimal("0.0000")).toBe("0.0000");
    expect(formatSignedDecimal(null)).toBe(MISSING);
  });
});

describe("compareDecimalStrings", () => {
  it("compares exactly across differing scales", () => {
    expect(compareDecimalStrings("0.3", "0.30")).toBe(0);
    expect(compareDecimalStrings("0.30000000000000004", "0.3")).toBe(1);
    expect(compareDecimalStrings("-1.5", "-1.25")).toBe(-1);
    expect(compareDecimalStrings("10", "9.9999")).toBe(1);
  });

  it("sorts missing values last", () => {
    expect(compareDecimalStrings(null, "1")).toBe(1);
    expect(compareDecimalStrings("1", null)).toBe(-1);
    expect(compareDecimalStrings(null, null)).toBe(0);
  });
});

describe("formatRatioAsPercent", () => {
  it("shifts the decimal point exactly", () => {
    expect(formatRatioAsPercent("0.1")).toBe("+10.00%");
    expect(formatRatioAsPercent("2")).toBe("+200.00%");
    expect(formatRatioAsPercent("-0.05")).toBe("-5.00%");
  });

  it("rounds half away from zero without float error", () => {
    expect(formatRatioAsPercent("0.00125")).toBe("+0.13%");
    expect(formatRatioAsPercent("0.001249")).toBe("+0.12%");
    expect(formatRatioAsPercent("-0.00125")).toBe("-0.13%");
  });

  it("handles long backend ratios and zero", () => {
    expect(formatRatioAsPercent("0.0833333333333333333333333333")).toBe("+8.33%");
    expect(formatRatioAsPercent("0")).toBe("0.00%");
    expect(formatRatioAsPercent("-0.00001")).toBe("0.00%");
    expect(formatRatioAsPercent(null)).toBe(MISSING);
  });
});

describe("dates and instants", () => {
  it("does not shift the calendar date", () => {
    expect(formatDate("2024-03-15")).toBe("2024-03-15");
    expect(formatDate(null)).toBe(MISSING);
  });

  it("renders a UTC instant from the string, keeping sub-seconds", () => {
    expect(formatUtcInstant("2024-03-15T11:00:00Z")).toBe("2024-03-15 11:00:00 UTC");
    expect(formatUtcInstant("2024-03-15T11:00:00.123456Z")).toBe("2024-03-15 11:00:00.123456 UTC");
    expect(formatUtcInstant("2024-03-15T11:00:00+00:00")).toBe("2024-03-15 11:00:00 UTC");
  });

  it("converts a non-UTC offset to the exact UTC instant", () => {
    expect(formatUtcInstant("2024-03-15T07:00:00-04:00")).toBe("2024-03-15 11:00:00 UTC");
  });

  it("shows a dash for null or garbage", () => {
    expect(formatUtcInstant(null)).toBe(MISSING);
    expect(formatUtcInstant("not a time")).toBe(MISSING);
  });
});
