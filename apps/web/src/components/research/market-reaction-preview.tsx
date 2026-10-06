import Link from "next/link";

import { ReturnsTable } from "@/components/research/returns-table";
import { SessionLadder } from "@/components/research/session-ladder";
import { ApiFailureState } from "@/components/ui/api-failure";
import { Frame } from "@/components/ui/frame";
import { getObservation } from "@/lib/api/research";
import { DEFAULT_HORIZON_TEXT, researchHref } from "@/lib/research-view";

/**
 * The event record's stock-only market-reaction preview: the DESCRIPTIVE study at the default
 * horizons, with no benchmark and no filing, from the same observation endpoint the workspace uses
 * and rendered by the same table and ladder. It is a preview, not a second workspace: choosing a
 * benchmark, other horizons or a filing happens in Research. It loads in its own boundary, so a
 * slow or failed read leaves the rest of the record untouched.
 */
export async function MarketReactionPreview({ eventId }: { readonly eventId: string }) {
  const result = await getObservation(eventId, {});
  const open = researchHref(eventId);

  if (result.kind !== "ok") {
    return (
      <Frame id="market-reaction" title="Market reaction" note="Descriptive preview, stock only.">
        <ApiFailureState failure={result} subject="the market-reaction preview" retryHref={`/earnings/${eventId}`} />
      </Frame>
    );
  }
  const study = result.data.descriptive.study;
  return (
    <Frame id="market-reaction" title="Market reaction" note={`Descriptive preview, stock only, horizons ${DEFAULT_HORIZON_TEXT}.`}>
      <div className="flex flex-col gap-3">
        <SessionLadder study={study} />
        <ReturnsTable study={study} caption="Stock returns by horizon" stockOnly />
        <p className="type-meta max-w-3xl">
          Split-adjusted price returns, dividends excluded. No benchmark is selected and no filing is linked here.{" "}
          <Link href={open} className="text-accent hover:underline">
            Open the research workspace
          </Link>{" "}
          to choose a benchmark, other horizons or a filing, and to see the point-in-time verdict and every refusal.
        </p>
      </div>
    </Frame>
  );
}
