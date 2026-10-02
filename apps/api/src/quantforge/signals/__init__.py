"""Signal layer: explicit, auditable trading signals derived from research facts.

This package answers a narrower question than :mod:`quantforge.research`:
*given what was knowable at a specific decision time, what signal would this
system have emitted?* It is deliberately downstream of, and structurally
separate from, the event-study/outcome machinery in
:mod:`quantforge.research.event_study` -- a signal is a decision input, never
a decision output, and the two must never share a type.

Signal != outcome
------------------
A signal may use only information available at its own decision time. It must
never use a forward stock return, a benchmark return, an abnormal return, or
any other figure from :class:`~quantforge.research.event_study.EventStudyResult`
-- those are outcomes, computed from prices that exist strictly after the
decision point, and exist only to *evaluate* a signal, never to produce one.
See :mod:`quantforge.signals.eps_surprise` for how this is enforced
structurally rather than by convention: the signal generator never
constructs, imports, or accepts an ``EventStudyResult`` or any of its forward-
looking sub-results.

Modules:

* :mod:`quantforge.signals.eps_surprise` -- the first signal family
  (Step 9.1/9.2/9.3): a sign-only classification of EPS surprise plus its
  continuous feature (``signal_value``), gated by the same point-in-time and
  session-alignment rules Step 8 already defines.
* :mod:`quantforge.signals.evaluation` -- signal evaluation (Step 9.4):
  joins an already-generated signal to an already-computed
  :class:`~quantforge.research.event_study.EventStudyResult`'s realized
  outcomes, horizon by horizon. This is where future returns are allowed to
  enter -- never into generation. See that module's docstring for the full
  generation/evaluation separation and the join-identity rules.
* :mod:`quantforge.signals.analytics` -- cross-event research analytics
  (Step 9.5): summarizes a population of already-built
  :class:`~quantforge.signals.evaluation.SignalEvaluation` objects --
  coverage, return statistics, hit rate, and a signal_value/abnormal_return
  correlation -- grouped by horizon and signal-direction cohort, and never
  pooling DESCRIPTIVE with POINT_IN_TIME. This is research analytics, not a
  backtest: no cumulative return, no portfolio, no Sharpe ratio.

No signal, evaluation, or analytics summary is persisted yet; this package
has no database table and this milestone adds no migration.
"""

from __future__ import annotations

from quantforge.signals.analytics import (
    CorrelationStatus,
    CoverageCounts,
    DecimalSummary,
    HitRateResult,
    SignalCohortSummary,
    SignalDirectionCohort,
    SignalValueAbnormalReturnCorrelation,
    summarize_signal_evaluations,
)
from quantforge.signals.eps_surprise import (
    EpsSurpriseDirection,
    EpsSurpriseSignal,
    EpsSurpriseSignalStatus,
    SignalType,
    classify_eps_surprise_direction,
    generate_eps_surprise_signal,
)
from quantforge.signals.evaluation import (
    SignalEvaluation,
    SignalEvaluationStatus,
    evaluate_eps_surprise_signal,
)

__all__ = [
    "CorrelationStatus",
    "CoverageCounts",
    "DecimalSummary",
    "EpsSurpriseDirection",
    "EpsSurpriseSignal",
    "EpsSurpriseSignalStatus",
    "HitRateResult",
    "SignalCohortSummary",
    "SignalDirectionCohort",
    "SignalEvaluation",
    "SignalEvaluationStatus",
    "SignalType",
    "SignalValueAbnormalReturnCorrelation",
    "classify_eps_surprise_direction",
    "evaluate_eps_surprise_signal",
    "generate_eps_surprise_signal",
    "summarize_signal_evaluations",
]
