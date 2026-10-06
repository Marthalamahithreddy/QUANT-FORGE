/**
 * Display formatting for backend values.
 *
 * Financial figures arrive as decimal strings and are NEVER converted to a JS
 * `number`. Everything here operates on the string (or on BigInt scaled
 * integers), so no digit is lost to binary floating point. Rounding appears
 * only where a value is explicitly shown at reduced precision, and the
 * unrounded value stays available to the caller.
 */

export const MISSING = "—";

// BigInt literals (`0n`) need an ES2020 target; the project targets lower, so
// constants are built with the BigInt() function instead.
const ZERO = BigInt(0);
const ONE = BigInt(1);
const TWO = BigInt(2);

/** 10^n as a BigInt, without the `**` operator (also gated by target). */
const pow10 = (n: number): bigint => BigInt(`1${"0".repeat(n)}`);

const DECIMAL = /^(-)?(\d+)(?:\.(\d+))?$/;

interface ParsedDecimal {
  readonly negative: boolean;
  readonly digits: bigint;
  /** Number of fractional digits in `digits`. */
  readonly scale: number;
}

function parseDecimal(raw: string): ParsedDecimal | null {
  const match = DECIMAL.exec(raw.trim());
  if (!match) {
    return null;
  }
  const fraction = match[3] ?? "";
  return {
    negative: match[1] === "-",
    digits: BigInt(`${match[2]}${fraction}`),
    scale: fraction.length,
  };
}

/** Whether `raw` is a plain decimal string (no exponent, no NaN). */
export function isDecimalString(raw: unknown): raw is string {
  return typeof raw === "string" && DECIMAL.test(raw.trim());
}

/**
 * Whether `raw` is a plain decimal string EXACTLY as the API sends it: no padding, no exponent.
 * A padded or exponent-form value is a contract violation to report, not something to repair.
 */
export function isExactDecimal(raw: unknown): raw is string {
  return isDecimalString(raw) && raw === raw.trim();
}

/** Shows a decimal string exactly as sent; `null` becomes an explicit dash. */
export function formatDecimal(raw: string | null): string {
  if (raw === null) {
    return MISSING;
  }
  return isDecimalString(raw) ? raw.trim() : MISSING;
}

/** A signed rendering (`+0.2000`) used for surprises, where direction matters. */
export function formatSignedDecimal(raw: string | null): string {
  const text = formatDecimal(raw);
  if (text === MISSING) {
    return text;
  }
  const parsed = parseDecimal(text);
  if (parsed && !parsed.negative && parsed.digits !== ZERO) {
    return `+${text}`;
  }
  return text;
}

/**
 * Renders a ratio string as a percentage with `places` decimals, using exact
 * integer arithmetic and round-half-away-from-zero. Display only.
 */
/** The percent magnitude at `places` decimals as an integer (rounded half away from zero). */
function percentMagnitude(parsed: ParsedDecimal, places: number): bigint {
  // value * 100 has `scale - 2` fractional digits; shift to `places`.
  const targetScale = parsed.scale - 2;
  if (targetScale >= places) {
    const divisor = pow10(targetScale - places);
    const quotient = parsed.digits / divisor;
    const remainder = parsed.digits % divisor;
    return remainder * TWO >= divisor ? quotient + ONE : quotient;
  }
  return parsed.digits * pow10(places - targetScale);
}

export function formatRatioAsPercent(raw: string | null, places = 2): string {
  if (raw === null) {
    return MISSING;
  }
  const parsed = parseDecimal(raw);
  if (!parsed) {
    return MISSING;
  }
  const scaled = percentMagnitude(parsed, places);
  const text = scaled.toString().padStart(places + 1, "0");
  const whole = text.slice(0, text.length - places);
  const fraction = places > 0 ? `.${text.slice(text.length - places)}` : "";
  const sign = scaled === ZERO ? "" : parsed.negative ? "-" : "+";
  return `${sign}${whole}${fraction}%`;
}

const ISO_DATE = /^(\d{4}-\d{2}-\d{2})$/;
const ISO_INSTANT = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d+)?(?:Z|[+-]00:?00)$/;

/** `2024-03-15`; passes the backend calendar date through unchanged. */
export function formatDate(raw: string | null): string {
  return raw !== null && ISO_DATE.test(raw) ? raw : MISSING;
}

/**
 * Formats an instant as `YYYY-MM-DD HH:MM:SS UTC` by reading the ISO string,
 * never through `Date`, so the viewer's timezone cannot shift the value.
 * Sub-second digits are kept.
 */
export function formatUtcInstant(raw: string | null): string {
  if (raw === null) {
    return MISSING;
  }
  const match = ISO_INSTANT.exec(raw);
  if (match) {
    return `${match[1]} ${match[2]}${match[3] ?? ""} UTC`;
  }
  // A non-UTC offset is converted exactly (an instant is an instant); only
  // sub-millisecond digits could be lost, which the API does not emit here.
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return MISSING;
  }
  const iso = parsed.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC`;
}

// ── Foundations for the V2 financial-value primitives (Step 11.1B) ────────────────────────
// Still string-only: nothing below converts a financial value to a JS number.

/**
 * Exact digits with the INTEGER part grouped in threes (`94836000000.0000` -> `94,836,000,000.0000`).
 * Lossless: no digit is rounded or dropped, and the fractional part (its scale) is untouched.
 * Anything that is not a plain decimal string is returned as the missing dash.
 */
export function formatGroupedDecimal(raw: string | null): string {
  if (raw === null) {
    return MISSING;
  }
  const match = DECIMAL.exec(raw.trim());
  if (!match) {
    return MISSING;
  }
  const grouped = (match[2] ?? "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${match[1] ?? ""}${grouped}${match[3] === undefined ? "" : `.${match[3]}`}`;
}

export type Direction = "positive" | "negative" | "zero";

/**
 * Above, below or at zero, read from the digits of the string (so `-0.00` is `zero`). `null` for
 * a value that is not a plain decimal string. This is what an optional direction tint reads; it
 * is a position relative to zero, never a judgement.
 */
export function directionOf(raw: string | null): Direction | null {
  if (raw === null) {
    return null;
  }
  const parsed = parseDecimal(raw);
  if (!parsed) {
    return null;
  }
  if (parsed.digits === ZERO) {
    return "zero";
  }
  return parsed.negative ? "negative" : "positive";
}

/**
 * True when a ratio is not zero but would be shown as `0.00%` at the default precision: such a
 * value must say so rather than look like an exact zero.
 */
export function ratioRoundsToZero(raw: string | null, places = 2): boolean {
  if (raw === null) {
    return false;
  }
  const parsed = parseDecimal(raw);
  if (!parsed || parsed.digits === ZERO) {
    return false;
  }
  // Non-zero input whose rounded display magnitude is zero, decided on the digits themselves.
  return percentMagnitude(parsed, places) === ZERO;
}
