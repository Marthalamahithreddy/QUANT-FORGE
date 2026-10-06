// @vitest-environment node
import { describe, expect, it } from "vitest";

import { axisScale, markPosition, slotCentre, tickIndexes, tickLabel, tickPosition } from "@/lib/chart/geometry";

describe("axisScale: a symmetric axis with round ticks", () => {
  it("covers the largest magnitude with headroom and is symmetric around zero", () => {
    const scale = axisScale(["0.1", "0.2", "-0.05"]);
    expect(scale.extent).toBeGreaterThan(20);
    expect(Math.min(...scale.ticks)).toBe(-scale.extent);
    expect(Math.max(...scale.ticks)).toBe(scale.extent);
    expect(scale.ticks).toContain(0);
    expect(scale.ticks.length).toBeLessThanOrEqual(9);
  });

  it.each([
    [["0.1", "0.2"], ["-30%", "-20%", "-10%", "0%", "10%", "20%", "30%"]],
    [["0.0123"], ["-1.5%", "-1%", "-0.5%", "0%", "0.5%", "1%", "1.5%"]],
    [["0.00004"], ["-0.006%", "-0.004%", "-0.002%", "0%", "0.002%", "0.004%", "0.006%"]],
    [["12.5"], ["-1500%", "-1000%", "-500%", "0%", "500%", "1000%", "1500%"]],
    [["0.003"], ["-0.4%", "-0.3%", "-0.2%", "-0.1%", "0%", "0.1%", "0.2%", "0.3%", "0.4%"]],
  ])("labels the axis scale from integers, with no float artefacts: %j", (values, labels) => {
    const scale = axisScale(values);
    expect(tickIndexes(scale).map((index) => tickLabel(scale, index))).toEqual(labels);
  });

  it("falls back to +/-1% when there is nothing to plot or everything is zero", () => {
    for (const input of [[], ["0"], ["0.0000"], ["not-a-number"]]) {
      const scale = axisScale(input);
      expect(scale.extent).toBeGreaterThanOrEqual(1);
      expect(scale.ticks).toContain(0);
    }
  });

  it("copes with a very large and a very small return", () => {
    expect(axisScale(["1000"]).extent).toBeGreaterThan(100000);
    expect(axisScale(["0.0000001"]).extent).toBeGreaterThan(0);
  });
});

describe("marks are placed, never reported", () => {
  const scale = axisScale(["0.1"]);
  const top = 20;
  const bottom = 220;

  it("puts zero on the middle, positive above and negative below", () => {
    expect(markPosition("0", scale, top, bottom)).toBe(120);
    expect(markPosition("0.1", scale, top, bottom)!).toBeLessThan(120);
    expect(markPosition("-0.1", scale, top, bottom)!).toBeGreaterThan(120);
  });

  it("is symmetric and monotonic", () => {
    const up = markPosition("0.05", scale, top, bottom)!;
    const down = markPosition("-0.05", scale, top, bottom)!;
    expect(120 - up).toBeCloseTo(down - 120, 9);
    expect(markPosition("0.08", scale, top, bottom)!).toBeLessThan(markPosition("0.04", scale, top, bottom)!);
  });

  it("clamps to the axis and refuses a non-number", () => {
    expect(markPosition("1000", scale, top, bottom)).toBe(top);
    expect(markPosition("-1000", scale, top, bottom)).toBe(bottom);
    expect(markPosition("n/a", scale, top, bottom)).toBeNull();
    expect(markPosition("", scale, top, bottom)).toBe(120); // "" is 0 to Number(); the caller never passes a null/blank value
  });

  it("tick positions and slot centres are plain pixel coordinates", () => {
    expect(tickPosition(0, scale, top, bottom)).toBe(120);
    expect(tickPosition(scale.extent, scale, top, bottom)).toBe(top);
    expect(slotCentre(0, 2, 0, 200)).toBe(50);
    expect(slotCentre(1, 2, 0, 200)).toBe(150);
  });
});
