import { Suspense } from "react";

import { AccountingSection } from "@/components/research/accounting-section";
import { BenchmarkLookup, benchmarkName } from "@/components/research/benchmark-lookup";
import { EventSummary } from "@/components/research/event-summary";
import { ModeFrame } from "@/components/research/mode-frame";
import { ProvenanceSection } from "@/components/research/provenance";
import { RefusalLedger, RefusalReference } from "@/components/research/refusal-ledger";
import { ResultStatusStrip } from "@/components/research/result-status-strip";
import { StudyView } from "@/components/research/study-view";
import { RefusalNotice } from "@/components/state/refusal-notice";
import { ApiFailureState } from "@/components/ui/api-failure";
import { Section } from "@/components/ui/section";
import { getObservation } from "@/lib/api/research";
import type { EarningsEvent } from "@/lib/api/schema";
import type { ResearchConfigState } from "@/lib/research-view";
import {
  DESCRIPTIVE_QUESTION,
  PIT_REFUSAL_SENTENCE,
  POINT_IN_TIME_QUESTION,
  POINT_IN_TIME_STATE_LABEL,
  SECTION_STATE_LABEL,
} from "@/lib/research-wording";
import { POINT_IN_TIME_STATE, SECTION_STATE } from "@/lib/state-map";

export interface ResearchResultsProps {
  readonly eventId: string;
  readonly record: EarningsEvent;
  readonly config: ResearchConfigState;
}

/**
 * The result region: ONE observation request carries both research modes, so nothing here asks the
 * API for a mode, a horizon or a section separately. The benchmark index is read in parallel, only
 * to NAME the selected benchmark: if it fails, the study is unaffected.
 *
 * A failed read (HTTP, network, contract) is an error state; a domain refusal is a section of a
 * successful response and is shown as one.
 */
export async function ResearchResults({ eventId, record, config }: ResearchResultsProps) {
  const result = await getObservation(eventId, config.query);

  if (result.kind !== "ok") {
    return (
      <ApiFailureState
        failure={result}
        subject="this research result"
        notFoundTitle="No stored event has this identifier"
        retryHref={config.canonical ?? undefined}
      />
    );
  }
  const observation = result.data;
  const id = config.query.benchmarkId;
  const idLabel = <span className="type-id">{id}</span>;
  // The benchmark is NAMED from the index in its own boundary: the study above never waits for it.
  const benchmarkLabel = id ? (
    <Suspense fallback={idLabel}>
      <BenchmarkLookup id={id} page={config.benchmarkPage}>
        {(item) => (item ? benchmarkName(item) : idLabel)}
      </BenchmarkLookup>
    </Suspense>
  ) : undefined;
  const { descriptive, point_in_time: pit, refusals } = observation;
  const configHref = "#research-config";
  const pitReason = refusals.find((entry) => entry.scope === "POINT_IN_TIME" && entry.code === pit.eligibility)?.reason;

  return (
    <div className="flex flex-col gap-5">
      <ResultStatusStrip observation={observation} />
      <EventSummary observation={observation} record={record} />

      <ModeFrame
        mode="DESCRIPTIVE"
        id="section-descriptive"
        question={DESCRIPTIVE_QUESTION}
        state={SECTION_STATE[descriptive.state]}
        stateText={SECTION_STATE_LABEL[descriptive.state]}
      >
        <StudyView study={descriptive.study} label="Descriptive" configHref={configHref} benchmarkLabel={benchmarkLabel} />
        <RefusalReference scopes={["MARKET", "BENCHMARK"]} refusals={refusals} />
      </ModeFrame>

      <ModeFrame
        mode="POINT_IN_TIME"
        id="section-point-in-time"
        question={POINT_IN_TIME_QUESTION}
        state={POINT_IN_TIME_STATE[pit.state]}
        stateText={POINT_IN_TIME_STATE_LABEL[pit.state]}
      >
        {pit.study !== null ? (
          <>
            <p className="type-meta">
              The availability evidence satisfies the point-in-time policy for this event, so the same computation is shown in point-in-time mode.
              Eligibility is an evidence-policy result, not a recommendation.
            </p>
            <StudyView study={pit.study} label="Point-in-time" configHref={configHref} benchmarkLabel={benchmarkLabel} />
            <RefusalReference scopes={["POINT_IN_TIME"]} refusals={refusals} />
          </>
        ) : (
          <RefusalNotice kind="REFUSED" title="Point-in-time study" reason={pitReason}>
            <p>
              Eligibility: <span className="type-id">{pit.eligibility}</span>. {PIT_REFUSAL_SENTENCE}
            </p>
          </RefusalNotice>
        )}
      </ModeFrame>

      <div id="section-accounting" className="scroll-mt-14">
        <Section title="Accounting and filing" note="Descriptive. Only a filing you supplied.">
          <AccountingSection observation={observation} configHref={configHref} />
        </Section>
      </div>

      <div id="refusal-ledger" className="scroll-mt-14">
        <Section title="Refusal ledger" note={`${refusals.length} ${refusals.length === 1 ? "refusal" : "refusals"}`}>
          <RefusalLedger refusals={refusals} />
        </Section>
      </div>

      <Section title="Provenance" note="Where each part came from.">
        <ProvenanceSection observation={observation} record={record} benchmarkPage={config.benchmarkPage} />
      </Section>
    </div>
  );
}
