/**
 * Calendar-date validity for `YYYY-MM-DD` text. Dates only: nothing financial passes through
 * here, which is why the numeric conversion lives in its own module instead of the decoders
 * (tests/contract-structure.test.ts forbids numeric coercion in the decoder layer).
 */

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Whether `text` is a real calendar date written `YYYY-MM-DD` (leap years included). */
export function isCalendarDate(text: string): boolean {
  const match = DATE.exec(text);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1] ?? 0;
  return day <= days;
}

const CLOCK = /^(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/;

/** Whether the time and UTC offset of an ISO instant (`HH:MM:SS[.f](Z|±HH:MM)`) are in range. */
export function isValidClock(text: string): boolean {
  const match = CLOCK.exec(text);
  if (!match) return false;
  const [hour, minute, second] = [Number(match[1]), Number(match[2]), Number(match[3])];
  if (hour > 23 || minute > 59 || second > 59) return false;
  return match[4] === undefined || (Number(match[4]) <= 23 && Number(match[5]) <= 59);
}
