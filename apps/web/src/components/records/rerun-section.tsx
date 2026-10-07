import { LinkButton } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import type { RecordDetail } from "@/lib/api/record-types";
import { pinsTheWorkspaceCannotCarry, rerunHref } from "@/lib/record-view";

/**
 * A link into the Research workspace, built from the stored request. It issues no request: opening
 * it starts a NEW live computation against today's stored data, in a page that does not call
 * anything a baseline. Only parameters the workspace accepts are carried (event, horizons,
 * benchmark, filing accession and its two selectors); no record id, fingerprint or evidence id is.
 */
export function RerunSection({ record }: { readonly record: RecordDetail }) {
  const pins = pinsTheWorkspaceCannotCarry(record.manifest.request);
  return (
    <Section id="rerun" title="Re-run live" note="A new computation, not this baseline.">
      <div className="flex flex-col gap-3">
        <p className="type-meta max-w-3xl">
          Opens the Research workspace with this record&rsquo;s request. That is a new live study of today&rsquo;s stored data: its numbers may differ from this baseline&rsquo;s,
          and nothing is requested until you open it.
        </p>
        {pins.length > 0 ? (
          <p className="type-meta max-w-3xl">
            This request also pinned {pins.join(", ")}. The workspace does not accept those, so the live study resolves them itself and may not use the same inputs.
          </p>
        ) : null}
        <div>
          <LinkButton href={rerunHref(record.request)}>Re-run this request live</LinkButton>
        </div>
      </div>
    </Section>
  );
}
