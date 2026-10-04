import Link from "next/link";

/** The restrained product identity: `QF/` in mono with an accent slash. No logo, no tagline. */
export function Wordmark() {
  return (
    <Link href="/" className="inline-flex w-fit items-baseline px-2 py-1 font-mono text-sm font-semibold tracking-tight text-text">
      <span aria-hidden="true">
        QF<span className="text-accent">/</span>
      </span>
      <span className="sr-only">QuantForge</span>
    </Link>
  );
}
