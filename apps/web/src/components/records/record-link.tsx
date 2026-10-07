import { AppLink } from "@/components/ui/app-link";
import type { RecordSummary } from "@/lib/api/schema";
import { recordPath } from "@/lib/record-view";
import { shortId } from "@/lib/record-wording";

/**
 * A saved research record as the link to its page: the short id on screen, the rest of the id for
 * assistive technology, so a list of records is never a list of identical-looking links.
 */
export function RecordLink({ record }: { readonly record: RecordSummary }) {
  const short = shortId(record.record_id);
  return (
    <AppLink href={recordPath(record.record_id)} className="type-id text-accent hover:underline">
      {short}
      <span aria-hidden="true">…</span>
      <span className="sr-only">{record.record_id.slice(short.length)}</span>
    </AppLink>
  );
}
