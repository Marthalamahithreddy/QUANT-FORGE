/** An identifier (a UUID, an accession-like code, a digest) as wrapped monospace text: shown whole, never cut. */
export function Id({ value }: { readonly value: string }) {
  return <code className="type-id break-all">{value}</code>;
}
