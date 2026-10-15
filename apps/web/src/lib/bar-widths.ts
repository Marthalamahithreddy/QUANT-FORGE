/**
 * Bar lengths for the current-versus-prior comparison, from exact decimal strings.
 *
 * Integer arithmetic only: each string is read as a scaled BigInt, so no financial value ever
 * becomes a floating-point number. The result is a whole PERCENT of the available width (0 to 100)
 * and is used for drawing only; every label comes from the exact string, never from a length. The
 * percent is returned as a decimal STRING ("0" to "100") so not even that integer passes through
 * `Number`.
 * (No BigInt literals: the TypeScript target is below ES2020, so values are built with `BigInt()`.)
 */

interface Scaled {
  readonly digits: bigint;
  readonly scale: number;
}

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

function scaled(exact: string): Scaled | null {
  const match = DECIMAL.exec(exact);
  if (match === null) return null;
  const fraction = match[3] ?? "";
  return { digits: BigInt(`${match[2]}${fraction}`), scale: fraction.length };
}

/** The magnitude of each value as a whole percent (0..100) of the largest magnitude; `null` for a non-decimal. */
export function barPercents(exactValues: readonly string[]): readonly (string | null)[] {
  const parsed = exactValues.map(scaled);
  const places = Math.max(0, ...parsed.map((value) => value?.scale ?? 0));
  const magnitudes = parsed.map((value) =>
    value === null ? null : value.digits * BigInt(10) ** BigInt(places - value.scale),
  );
  let largest = BigInt(0);
  for (const magnitude of magnitudes) {
    if (magnitude !== null && magnitude > largest) largest = magnitude;
  }
  return magnitudes.map((magnitude) =>
    magnitude === null ? null : largest === BigInt(0) ? "0" : ((magnitude * BigInt(100)) / largest).toString(),
  );
}
