import { AppLink } from "@/components/ui/app-link";
import type { FilingIndexItem } from "@/lib/api/schema";
import { filingDetailHref } from "@/lib/filings-view";

/**
 * A filing's accession as the link to its page. A joint accession is stored under more than one
 * registrant, so the link's name carries the form and the CIK that tell the rows apart.
 */
export function AccessionLink({ filing }: { readonly filing: FilingIndexItem }) {
  return (
    <AppLink
      href={filingDetailHref(filing)}
      aria-label={`Filing ${filing.accession}, ${filing.form}${filing.company_cik ? `, CIK ${filing.company_cik}` : ""}`}
      className="type-id text-accent underline-offset-2 hover:underline"
    >
      {filing.accession}
    </AppLink>
  );
}
