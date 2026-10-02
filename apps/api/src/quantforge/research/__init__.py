"""Research layer: point-in-time-correct reasoning over ingested facts.

This package answers questions *about* the data QuantForge has already
ingested -- when an earnings event maps to a trading session, whether an
observation could have been known at a given decision time, what the EPS
surprise was, what happened to the stock and the benchmark afterward, what
the abnormal return was -- without touching the ingestion pipeline or
inventing new facts. Nothing here writes to the database; it reads what
ingestion already wrote and reasons about it.

Modules are kept separate on purpose, because they answer different questions
and have different testing needs:

* :mod:`quantforge.research.calendar` -- pure, deterministic session-alignment
  arithmetic over an already-known, already-ordered list of trading sessions.
* :mod:`quantforge.research.availability` -- pure point-in-time eligibility
  and revision-selection rules.
* :mod:`quantforge.research.listing_resolution` -- the database read that
  resolves which listing an event's issuer traded under on a given date.
* :mod:`quantforge.research.alignment` composes the three above into
  :func:`~quantforge.research.alignment.align_earnings_event` (Step 8.1/8.2).
* :mod:`quantforge.research.eps_surprise` -- pure EPS-surprise arithmetic,
  gated by the same point-in-time rules (Step 8.3).
* :mod:`quantforge.research.returns` -- pure close-to-close forward-return
  arithmetic over an ordered price series (Step 8.4).
* :mod:`quantforge.research.price_series` -- the database read that resolves
  one coherent, single-source close-price series for a listing/window.
* :mod:`quantforge.research.event_returns` composes the alignment result with
  the latter two into :func:`~quantforge.research.event_returns.compute_event_forward_returns`.
* :mod:`quantforge.research.benchmark_returns` -- pure benchmark-return
  arithmetic, matched to the stock's own anchor/target session dates exactly
  rather than counting benchmark sessions independently (Step 8.5).
* :mod:`quantforge.research.benchmark_price_series` -- the database read that
  resolves one coherent, single-source benchmark level series.
* :mod:`quantforge.research.abnormal_returns` -- pure, simple
  market-adjusted abnormal-return arithmetic (stock return minus benchmark
  return).
* :mod:`quantforge.research.event_study` composes everything above into
  :func:`~quantforge.research.event_study.analyze_earnings_event` (Step 8.6),
  the single entry point for a complete earnings event study.

None of these pure/no-database modules import SQLAlchemy; none of the
database-facing modules contain financial arithmetic.
"""

from __future__ import annotations

from quantforge.research.abnormal_returns import (
    AbnormalReturn,
    AbnormalReturnStatus,
    compute_abnormal_return,
)
from quantforge.research.alignment import EventStudyAlignment, align_earnings_event
from quantforge.research.availability import (
    PointInTimeEligibility,
    ResearchMode,
    classify_point_in_time_eligibility,
    select_known_as_of,
)
from quantforge.research.benchmark_price_series import fetch_coherent_benchmark_price_series
from quantforge.research.benchmark_returns import (
    BenchmarkReturn,
    BenchmarkReturnStatus,
    compute_benchmark_return_for_stock_return,
    no_benchmark_selected,
)
from quantforge.research.calendar import AlignmentStatus, SessionAlignment, align_event_to_sessions
from quantforge.research.eps_surprise import (
    EpsSurpriseResult,
    EpsSurpriseStatus,
    compute_eps_surprise,
    compute_eps_surprise_for_observation,
)
from quantforge.research.event_returns import DEFAULT_HORIZONS, compute_event_forward_returns
from quantforge.research.event_study import (
    PRICE_BASIS_NOTE,
    EventStudyResult,
    analyze_earnings_event,
)
from quantforge.research.listing_resolution import (
    ListingResolution,
    ListingResolutionStatus,
    resolve_company_listing_for_date,
)
from quantforge.research.price_series import (
    PriceSeriesResolution,
    PriceSeriesStatus,
    fetch_coherent_price_series,
)
from quantforge.research.returns import (
    EventForwardReturns,
    ForwardReturn,
    ForwardReturnStatus,
    compute_forward_returns,
    no_alignment_forward_returns,
)

__all__ = [
    "DEFAULT_HORIZONS",
    "PRICE_BASIS_NOTE",
    "AbnormalReturn",
    "AbnormalReturnStatus",
    "AlignmentStatus",
    "BenchmarkReturn",
    "BenchmarkReturnStatus",
    "EpsSurpriseResult",
    "EpsSurpriseStatus",
    "EventForwardReturns",
    "EventStudyAlignment",
    "EventStudyResult",
    "ForwardReturn",
    "ForwardReturnStatus",
    "ListingResolution",
    "ListingResolutionStatus",
    "PointInTimeEligibility",
    "PriceSeriesResolution",
    "PriceSeriesStatus",
    "ResearchMode",
    "SessionAlignment",
    "align_earnings_event",
    "align_event_to_sessions",
    "analyze_earnings_event",
    "classify_point_in_time_eligibility",
    "compute_abnormal_return",
    "compute_benchmark_return_for_stock_return",
    "compute_eps_surprise",
    "compute_eps_surprise_for_observation",
    "compute_event_forward_returns",
    "compute_forward_returns",
    "fetch_coherent_benchmark_price_series",
    "fetch_coherent_price_series",
    "no_alignment_forward_returns",
    "no_benchmark_selected",
    "resolve_company_listing_for_date",
    "select_known_as_of",
]
