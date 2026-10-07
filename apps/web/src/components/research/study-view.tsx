import type { ReactNode } from "react";

import { EpsSurpriseValue } from "@/components/financial/eps-surprise";
import { HorizonReturnsChart } from "@/components/research/horizon-returns-chart";
import { ReturnsTable } from "@/components/research/returns-table";
import { SessionLadder } from "@/components/research/session-ladder";
import { RefusalNotice } from "@/components/state/refusal-notice";
import { Callout } from "@/components/ui/callout";
import { KeyValue, KeyValueList } from "@/components/ui/key-value";
import type { Study } from "@/lib/api/schema";
import { benchmarkRequested, horizonRows } from "@/lib/research-rows";

function SubHeading({ children }: { readonly children: string }) {
  return <h3 className="text-xs font-medium tracking-wide text-text-2 uppercase">{children}</h3>;
}

export interface StudyViewProps {
  readonly study: Study;
  /** Scopes the table/chart names ("Descriptive", "Point-in-time"). */
  readonly label: string;
  /** Where "select a benchmark" leads: the configuration controls. Absent for a stored study, which has none. */
  readonly configHref?: string;
  /** How the selected benchmark is named (streamed separately: the study never waits for it). */
  readonly benchmarkLabel?: ReactNode;
}

/**
 * One event-study result, rendered whole and exactly as supplied: alignment, return by horizon
 * (chart, then its authoritative table), the benchmark state and the EPS surprise. Used for the
 * descriptive study and, when the evidence is eligible, for the point-in-time one: the same
 * component, so the two modes can never drift apart in how they show a number.
 *
 * Nothing is recomputed in React. The abnormal return is the backend's figure; no benchmark is
 * chosen or implied; a horizon that could not be computed is stated, never drawn.
 */
export function StudyView({ study, label, configHref, benchmarkLabel }: StudyViewProps) {
  const requested = benchmarkRequested(study);
  const anyStock = horizonRows(study).some((row) => row.stock.return_value !== null);
  return (
    <>
      <div className="flex flex-col gap-2">
        <SubHeading>Session alignment</SubHeading>
        <SessionLadder study={study} label={label} />
      </div>

      <div className="flex flex-col gap-2">
        <SubHeading>Return by horizon</SubHeading>
        {requested ? (
          <p className="type-meta">
            Benchmark: {benchmarkLabel ?? <span className="type-id">{study.benchmark.benchmark_id}</span>}.{" "}
            {configHref ? "Selected by you" : "Selected in this request"}; the abnormal return is the backend&rsquo;s stock-minus-benchmark figure.
          </p>
        ) : (
          <RefusalNotice kind="NOT_REQUESTED" title="Benchmark-relative and abnormal returns" reason="No benchmark was selected. Nothing is chosen for you.">
            Stock returns are valid without one.
            {configHref ? (
              <>
                {" "}
                <a href={configHref} className="text-accent hover:underline">
                  Select a benchmark
                </a>
              </>
            ) : null}
          </RefusalNotice>
        )}
        {anyStock ? (
          <HorizonReturnsChart study={study} title={`${label} return by horizon`} />
        ) : (
          <Callout role="note" label="No chart">
            <p>
              No chart: no horizon has a stock return, so there is nothing to place. The table lists each horizon&rsquo;s status and reason.
            </p>
          </Callout>
        )}
        <ReturnsTable study={study} caption={`${label} returns by horizon`} />
        <details className="type-meta">
          <summary className="cursor-pointer text-text-2 max-lg:min-h-11">Price basis and method note (from the API)</summary>
          <p className="mt-1 max-w-3xl">{study.price_basis_note}</p>
        </details>
      </div>

      <div className="flex flex-col gap-2">
        <SubHeading>EPS surprise</SubHeading>
        <KeyValueList>
          <KeyValue label="Surprise">
            <EpsSurpriseValue
              status={study.eps_surprise.status}
              absolute={study.eps_surprise.absolute_surprise}
              ratio={study.eps_surprise.surprise_ratio}
              reason={study.eps_surprise.reason}
            />
          </KeyValue>
        </KeyValueList>
      </div>
    </>
  );
}
