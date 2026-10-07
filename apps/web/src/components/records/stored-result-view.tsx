import { StoredAccounting } from "@/components/records/stored-accounting";
import { EventFacts } from "@/components/research/event-summary";
import { ModeFrame } from "@/components/research/mode-frame";
import { StudyView } from "@/components/research/study-view";
import { RefusalNotice } from "@/components/state/refusal-notice";
import { Frame } from "@/components/ui/frame";
import { Section } from "@/components/ui/section";
import type { StoredResult } from "@/lib/api/record-types";
import { PIT_PRESENTATION } from "@/lib/availability";
import { DESCRIPTIVE_QUESTION, PIT_REFUSAL_SENTENCE, POINT_IN_TIME_QUESTION } from "@/lib/research-wording";
import { PIT_STATE } from "@/lib/state-map";
import { storedStudyToStudy } from "@/lib/stored-result";

/**
 * The stored result, shown semantically with the same components as the live workspace (the event
 * facts, the study with its chart, table and session ladder, and the accounting), fed from the
 * STORED fields through pure adapters. It is never routed through the live observation endpoint
 * and nothing current is merged into it.
 *
 * The stored result has no section state and no refusal ledger (those are presentation the live
 * API adds), so neither is shown: no state is invented, and a refused point-in-time study says so
 * from the stored eligibility alone.
 */
export function StoredResultView({ result }: { readonly result: StoredResult }) {
  const descriptive = storedStudyToStudy(result.descriptive.study);
  const pit = result.point_in_time;
  const presentation = PIT_PRESENTATION[pit.eligibility];
  return (
    <Section id="section-result" title="Stored result" note="The output persisted with this baseline.">
      <div className="flex flex-col gap-5">
        <Frame title="Event" note="As stored in the baseline.">
          <EventFacts event={result.event} resolution={result.identity.listing_resolution} />
        </Frame>

        <ModeFrame mode="DESCRIPTIVE" id="stored-descriptive" question={DESCRIPTIVE_QUESTION}>
          <StudyView study={descriptive} label="Stored descriptive" />
        </ModeFrame>

        <ModeFrame
          mode="POINT_IN_TIME"
          id="stored-point-in-time"
          question={POINT_IN_TIME_QUESTION}
          state={PIT_STATE[pit.eligibility]}
          stateText={presentation.label}
        >
          {pit.study !== null ? (
            <>
              <p className="type-meta">
                The stored availability evidence satisfied the point-in-time policy, so the same computation was stored in point-in-time mode. Eligibility is an
                evidence-policy result, not a recommendation.
              </p>
              <StudyView study={storedStudyToStudy(pit.study)} label="Stored point-in-time" />
            </>
          ) : (
            <RefusalNotice kind="REFUSED" title="Point-in-time study">
              <p>
                Eligibility: <span className="type-id">{pit.eligibility}</span>. {presentation.short}. {PIT_REFUSAL_SENTENCE}
              </p>
            </RefusalNotice>
          )}
        </ModeFrame>

        <section aria-label="Stored filing and accounting" className="flex flex-col gap-3 border-t border-border-subtle pt-3">
          <h3 className="type-section">Accounting and filing</h3>
          <StoredAccounting result={result} />
        </section>
      </div>
    </Section>
  );
}
