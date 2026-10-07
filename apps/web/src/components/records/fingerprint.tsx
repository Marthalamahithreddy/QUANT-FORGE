import { CopyValue } from "@/components/ui/copy-value";
import { SHORT_FINGERPRINT_LENGTH, shortFingerprint } from "@/lib/record-wording";

/**
 * A fingerprint shortened for a dense table. Only the DISPLAY is short: the remainder stays in the
 * document (visually hidden, so it is read aloud and selectable) and the full value is the hover
 * title, so nothing is altered or lost. Which fingerprint it is comes from the column header.
 */
export function FingerprintShort({ value }: { readonly value: string }) {
  return (
    <code className="type-id" title={value}>
      {shortFingerprint(value)}
      <span className="sr-only">{value.slice(SHORT_FINGERPRINT_LENGTH)}</span>
      <span aria-hidden="true">…</span>
    </code>
  );
}

/** The complete fingerprint, wrapped (never cut), with a copy action that copies exactly this text. */
export function FingerprintFull({ value, label }: { readonly value: string; readonly label: string }) {
  return (
    <span className="flex flex-col gap-1">
      <code className="type-id break-all">{value}</code>
      <CopyValue value={value} label={label} />
    </span>
  );
}
