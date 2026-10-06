/**
 * Chart geometry: the ONLY place an exact decimal string becomes a JS number, and only to place a
 * mark on a canvas.
 *
 * The boundary, enforced by tests/chart-boundary.test.ts:
 *
 * - `markPosition` parses a DISPLAY COPY of a return string to find a y coordinate. Nothing here
 *   returns, stores or formats that number as a financial value; coordinates are pixels.
 * - Every label of a data value comes from the exact string (see `formatRatioAsPercent`), never
 *   from a coordinate. The tick labels below label the AXIS SCALE (round numbers the axis is
 *   drawn in), are built with integer arithmetic only, and are never compared with, summed with
 *   or substituted for a data value.
 * - Nothing in this module feeds refusal logic, point-in-time logic or any derived return: the
 *   abnormal return is the backend's own figure and is never recomputed from marks.
 */

export interface AxisScale {
  /** Tick positions in axis units (percent), ascending, symmetric around zero. */
  readonly ticks: readonly number[];
  /** The half-range in axis units: the axis spans `-extent` to `+extent`. */
  readonly extent: number;
  /** One tick step is `mantissa * 10^exponent` percent (integers: labels never use a float). */
  readonly mantissa: 1 | 2 | 5;
  readonly exponent: number;
}

const MANTISSAS = [1, 2, 5] as const;

/** A display copy of a ratio string, in percent units. `null` for anything that is not a number. */
function percentOf(exactRatio: string): number | null {
  const value = Number(exactRatio) * 100;
  return Number.isFinite(value) ? value : null;
}

/**
 * A symmetric axis around zero with "nice" ticks that covers the largest magnitude among the
 * given ratio strings. With nothing to plot (or all zeros) the axis is +/-1%.
 */
export function axisScale(exactRatios: readonly string[]): AxisScale {
  let largest = 0;
  for (const ratio of exactRatios) {
    const percent = percentOf(ratio);
    if (percent !== null && Math.abs(percent) > largest) largest = Math.abs(percent);
  }
  if (largest === 0) largest = 1;
  // Smallest step of the form {1,2,5} x 10^e that keeps the half-range within 4 ticks.
  const exponentStart = Math.floor(Math.log10(largest / 4));
  for (let exponent = exponentStart; ; exponent += 1) {
    for (const mantissa of MANTISSAS) {
      const step = mantissa * 10 ** exponent;
      // Strictly beyond the largest value, so the extreme mark never sits on the axis edge.
      const count = Math.floor(largest / step + 1e-9) + 1;
      if (count <= 4) {
        const ticks: number[] = [];
        for (let index = -count; index <= count; index += 1) ticks.push(index * step);
        return { ticks, extent: count * step, mantissa, exponent };
      }
    }
  }
}

/**
 * The label of tick number `index` (counted from zero, so negative below): an exact string such as
 * `-0.5%` or `20%`, built from integers and a decimal exponent, never from a float.
 */
export function tickLabel(scale: AxisScale, index: number): string {
  if (index === 0) return "0%";
  const units = Math.abs(index) * scale.mantissa;
  const sign = index < 0 ? "-" : "";
  if (scale.exponent >= 0) return `${sign}${units}${"0".repeat(scale.exponent)}%`;
  const digits = String(units).padStart(-scale.exponent + 1, "0");
  const whole = digits.slice(0, digits.length + scale.exponent);
  const fraction = digits.slice(digits.length + scale.exponent).replace(/0+$/, "");
  return `${sign}${whole}${fraction ? `.${fraction}` : ""}%`;
}

/** The index of a tick in `scale.ticks` relative to zero (the middle tick). */
export const tickIndexes = (scale: AxisScale): readonly number[] => scale.ticks.map((_, position) => position - (scale.ticks.length - 1) / 2);

/**
 * The y coordinate (pixels, growing downward) of a ratio string on an axis drawn between `top` and
 * `bottom`, or `null` when the string is not a plottable number. Display copy only.
 */
export function markPosition(exactRatio: string, scale: AxisScale, top: number, bottom: number): number | null {
  const percent = percentOf(exactRatio);
  if (percent === null) return null;
  const clamped = Math.max(-scale.extent, Math.min(scale.extent, percent));
  const middle = (top + bottom) / 2;
  return middle - (clamped / scale.extent) * ((bottom - top) / 2);
}

/** The y coordinate of an axis-unit position (a tick), in pixels. */
export function tickPosition(percentUnits: number, scale: AxisScale, top: number, bottom: number): number {
  const middle = (top + bottom) / 2;
  return middle - (percentUnits / scale.extent) * ((bottom - top) / 2);
}

/** x coordinate of the centre of equally spaced categorical slot `index` of `count`. */
export function slotCentre(index: number, count: number, left: number, right: number): number {
  return left + ((index + 0.5) / count) * (right - left);
}
