/**
 * Adapters from the STORED result shape to the shapes the Research display components already
 * take (the event-study chart, the returns table, the session ladder and the EPS panels).
 *
 * These are restructurings and nothing else. Every figure is moved across as the exact string it
 * was stored as: there is no arithmetic, no comparison and no default. A stored study is NOT
 * routed through the live observation endpoint, and no current value is merged in: what is shown
 * is what was persisted.
 *
 * The only constants added are the two the API documents as constant on the live response and
 * that describe the very same stored fields (`comparative.meaning` and
 * `change.percentage_semantics`), so the shared panels can render a stored change.
 */

import type { StoredEpsChange, StoredFact, StoredStudy } from "@/lib/api/record-types";
import type { ComparativeEps, CurrentEps, EpsChange, FactEvidence, Study } from "@/lib/api/schema";

/** A stored study as the shape the Research study components render. */
export function storedStudyToStudy(study: StoredStudy): Study {
  return {
    research_mode: study.research_mode,
    point_in_time_eligibility: study.point_in_time_eligibility,
    eps_surprise: study.eps_surprise,
    alignment: study.alignment,
    stock_returns: study.stock_returns,
    benchmark: { benchmark_id: study.benchmark_id, returns: [...study.benchmark_returns] },
    // The stored per-horizon entries decode to the same four fields the live ones have
    // (horizon, status, the backend's abnormal return, reason): passed through, never recomputed.
    abnormal_returns: [...study.abnormal_returns],
    price_basis_note: study.price_basis_note,
  };
}

function factOf(fact: StoredFact): FactEvidence {
  return {
    fact_id: fact.fact_id,
    tag: fact.tag,
    taxonomy_version: fact.taxonomy_version,
    unit: fact.unit,
    period_end: fact.period_end,
    qtrs: fact.duration_quarters,
    value: fact.value,
    segments: fact.segments,
    coreg: fact.coreg,
  };
}

export interface StoredEpsPanels {
  readonly current: CurrentEps;
  readonly comparative: ComparativeEps;
  readonly change: EpsChange;
}

/** The stored EPS change as the three panels the filing pages already use. */
export function storedEpsPanels(change: StoredEpsChange): StoredEpsPanels {
  const current = change.comparative.current;
  // The decoder guarantees a stored fact has its whole identity or none of it.
  const currentFact: FactEvidence | null =
    current.fact_id === null
      ? null
      : factOf({
          fact_id: current.fact_id,
          tag: current.tag as string,
          taxonomy_version: current.taxonomy_version as string,
          unit: current.unit as string,
          period_end: current.period_end as string,
          duration_quarters: current.duration_quarters as number,
          value: current.value,
          segments: current.segments as string,
          coreg: current.coreg as string,
        });
  return {
    current: { status: current.status, reason: current.reason, fact: currentFact },
    comparative: {
      status: change.comparative.status,
      reason: change.comparative.reason,
      meaning: "comparative EPS as presented in this filing",
      fact: change.comparative.comparative_fact === null ? null : factOf(change.comparative.comparative_fact),
    },
    change: {
      status: change.status,
      eps_change: change.eps_change,
      transition: change.transition,
      percentage_change: change.percentage_change,
      percentage_semantics: "ratio",
      percentage_unavailable_reason: change.percentage_unavailable_reason,
    },
  };
}
