"""Step 8.6: the unified earnings event-study entry point.

:func:`analyze_earnings_event` answers the single composite question Step
8.1-8.5 were each built to answer one piece of: *for this selected earnings
observation, what was the EPS surprise, how did it align to a trading
session, what did the stock do afterward, what did the benchmark do over the
exact same interval, and what was the abnormal return?*

It is a **composition**, not a reimplementation. Every number in the result
comes from calling an existing Step 8.1-8.5 function; nothing here
re-derives alignment, re-counts sessions, or recomputes arithmetic a
dedicated module already owns:

========================================  =========================================
Question                                  Answered by
========================================  =========================================
Was this observation knowable at T?       :mod:`quantforge.research.availability`
What was the EPS surprise?                :mod:`quantforge.research.eps_surprise`
Which session does the event map to?      :mod:`quantforge.research.alignment`
What did the stock do afterward?          :mod:`quantforge.research.event_returns`
What did the benchmark do, same interval? :mod:`quantforge.research.benchmark_returns`
                                           + :mod:`quantforge.research.benchmark_price_series`
What was the abnormal return?             :mod:`quantforge.research.abnormal_returns`
========================================  =========================================

Revision selection happens before this function, not inside it
----------------------------------------------------------------
This function takes one already-selected
:class:`~quantforge.models.earnings.EarningsEvent` -- it does not query for
candidate revisions and does not choose among them. When a caller has several
revisions of the same fiscal period, it must pick one first, with
:func:`quantforge.research.availability.select_known_as_of` for a
point-in-time-correct choice. Duplicating that selection logic inside this
orchestrator would create a second, competing place where "which revision is
the right one" could be decided, and the two could disagree. There is exactly
one authoritative policy, and it lives in
:mod:`quantforge.research.availability`.

Benchmark selection is always explicit
-----------------------------------------
``benchmark_id`` defaults to ``None``, which means "no benchmark" --
:func:`quantforge.research.benchmark_returns.no_benchmark_selected` is used,
never a guessed index. There is no "US equity listing implies S&P 500" or
similar heuristic anywhere in this module.

Outcomes never feed back into inputs
--------------------------------------
Stock and benchmark prices after the event are *outcomes* being measured, not
*inputs* to the decision of what was knowable. This function calls
``align_earnings_event`` and ``compute_eps_surprise_for_observation`` using
only the event's own fields and fetches forward prices strictly afterward, in
that order, so a later price can never influence the alignment, the
eligibility classification, or the EPS surprise it is being compared against.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from sqlalchemy.orm import Session

from quantforge.models.earnings import EarningsEvent
from quantforge.research.abnormal_returns import AbnormalReturn, compute_abnormal_return
from quantforge.research.alignment import EventStudyAlignment, align_earnings_event
from quantforge.research.availability import (
    PointInTimeEligibility,
    ResearchMode,
    classify_point_in_time_eligibility,
)
from quantforge.research.benchmark_price_series import fetch_coherent_benchmark_price_series
from quantforge.research.benchmark_returns import (
    BenchmarkReturn,
    BenchmarkReturnStatus,
    compute_benchmark_return_for_stock_return,
    no_benchmark_selected,
)
from quantforge.research.eps_surprise import EpsSurpriseResult, compute_eps_surprise_for_observation
from quantforge.research.event_returns import DEFAULT_HORIZONS, compute_event_forward_returns
from quantforge.research.price_series import PriceSeriesStatus
from quantforge.research.returns import EventForwardReturns, ForwardReturnStatus

__all__ = ["PRICE_BASIS_NOTE", "EventStudyResult", "analyze_earnings_event"]

#: Documents, in the result itself, exactly what the returns in this study do
#: and do not represent -- so a caller cannot reasonably mistake a
#: split-adjusted, dividend-excluding price return for a dividend-reinvested
#: total return, and cannot mistake a plain subtraction for a modelled alpha.
#: See `quantforge.research.returns` and `quantforge.research.abnormal_returns`
#: for the full reasoning this summarises.
PRICE_BASIS_NOTE: Final[str] = (
    "Stock returns use DailyPriceBar.close; benchmark returns use "
    "BenchmarkPriceBar.close. Massive's daily-aggregate request sets "
    "adjusted=true, which (per Polygon's own aggregates contract, which "
    "Massive -- formerly Polygon.io -- uses as-is) means split-adjusted, not "
    "dividend-adjusted and not total-return. So both series are already "
    "split-adjusted price series; cash dividends are excluded from both. "
    "DailyPriceBar.adjusted_close stays unconditionally NULL under the "
    "current ingestion path and must not be read as a further adjustment on "
    "top of close; BenchmarkPriceBar has no adjusted-level column at all. "
    "These are therefore split-adjusted price returns, dividends excluded -- "
    "not dividend-reinvested total returns. The abnormal return is a simple "
    "market-adjusted figure (stock return minus benchmark return, implicit "
    "beta of 1) -- it is not a CAPM alpha, a factor alpha, or a "
    "regression-based excess return."
)


@dataclass(frozen=True, slots=True)
class EventStudyResult:
    """The complete, composed result of one earnings event study.

    Sub-results are preserved whole rather than flattened, so a caller that
    needs the detail -- which listing was resolved, why a horizon was
    unavailable, which price source was used -- can inspect the same typed
    objects Step 8.1-8.5 already define, instead of this type re-declaring
    dozens of fields that would drift from their originals.
    """

    event_id: uuid.UUID
    event_fingerprint: str
    company_id: uuid.UUID
    research_mode: ResearchMode
    point_in_time_eligibility: PointInTimeEligibility
    eps_surprise: EpsSurpriseResult
    alignment: EventStudyAlignment
    stock_returns: EventForwardReturns
    benchmark_id: uuid.UUID | None
    benchmark_returns: list[BenchmarkReturn]
    abnormal_returns: list[AbnormalReturn]
    price_basis_note: str


def _compute_benchmark_returns(
    session: Session,
    *,
    benchmark_id: uuid.UUID,
    stock_returns: EventForwardReturns,
    source_id: uuid.UUID | None,
) -> list[BenchmarkReturn]:
    available = [
        forward_return
        for forward_return in stock_returns.forward_returns
        if forward_return.status is ForwardReturnStatus.AVAILABLE
        and forward_return.anchor_session is not None
        and forward_return.target_session is not None
    ]

    prices: dict[date, Decimal] = {}
    ambiguous = False
    if available:
        window_start = min(fr.anchor_session for fr in available if fr.anchor_session is not None)
        window_end = max(fr.target_session for fr in available if fr.target_session is not None)
        price_resolution = fetch_coherent_benchmark_price_series(
            session,
            benchmark_id=benchmark_id,
            start=window_start,
            end=window_end,
            source_id=source_id,
        )
        ambiguous = price_resolution.status is PriceSeriesStatus.AMBIGUOUS_PRICE_SOURCE
        if not ambiguous:
            prices = dict(price_resolution.series)

    if ambiguous:
        return [
            BenchmarkReturn(
                benchmark_id=benchmark_id,
                horizon=forward_return.horizon,
                anchor_session=None,
                anchor_close=None,
                target_session=None,
                target_close=None,
                benchmark_return=None,
                status=BenchmarkReturnStatus.AMBIGUOUS_BENCHMARK_PRICE_SOURCE,
                reason=(
                    "more than one source reports this benchmark in the window "
                    "needed for this study; pass an explicit benchmark_source_id"
                ),
            )
            for forward_return in stock_returns.forward_returns
        ]

    return [
        compute_benchmark_return_for_stock_return(
            benchmark_id=benchmark_id, stock_return=forward_return, prices=prices
        )
        for forward_return in stock_returns.forward_returns
    ]


def analyze_earnings_event(  # noqa: PLR0913 - one explicit knob per independent Step 8 choice; see the docstring
    session: Session,
    event: EarningsEvent,
    *,
    mode: ResearchMode,
    security_id: uuid.UUID | None = None,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    stock_source_id: uuid.UUID | None = None,
    benchmark_id: uuid.UUID | None = None,
    benchmark_source_id: uuid.UUID | None = None,
) -> EventStudyResult:
    """Run the complete Step 8 event study for one already-selected earnings observation.

    Args:
        session: Database session to read listings, prices and benchmark
            levels from.
        event: The earnings observation to study. Already selected by the
            caller -- see the module docstring on why revision selection
            happens before this call, via
            :func:`quantforge.research.availability.select_known_as_of`.
        mode: Which research question is being asked. Propagated unchanged
            into the EPS surprise and the alignment -- see
            :mod:`quantforge.research.availability` for what each mode
            permits.
        security_id: Passed through to
            :func:`quantforge.research.alignment.align_earnings_event` to
            disambiguate a multi-security issuer.
        horizons: Trading-session horizons to measure. Every horizon
            produces one stock return, one benchmark return and one abnormal
            return, regardless of outcome.
        stock_source_id: Restrict the stock price series to one
            :class:`~quantforge.models.provenance.DataSource`, bypassing its
            ambiguity check. See
            :func:`quantforge.research.price_series.fetch_coherent_price_series`.
        benchmark_id: The benchmark to compare against. ``None`` means no
            benchmark was requested -- every benchmark and abnormal return
            will carry ``NO_BENCHMARK``/``BENCHMARK_RETURN_UNAVAILABLE``.
            Never defaulted to an inferred index.
        benchmark_source_id: Restrict the benchmark price series to one
            source, bypassing its own ambiguity check.

    Returns:
        The composed :class:`EventStudyResult`. A horizon failing for the
        stock, the benchmark, or both never aborts the study -- every
        requested horizon always produces an entry in ``stock_returns``,
        ``benchmark_returns`` and ``abnormal_returns``, carrying its own
        status.
    """
    horizons_list = list(horizons)

    eligibility = classify_point_in_time_eligibility(mode, observed_at=event.observed_at)

    eps_surprise = compute_eps_surprise_for_observation(
        reported_eps=event.reported_eps,
        estimated_eps=event.estimated_eps,
        observed_at=event.observed_at,
        mode=mode,
    )

    alignment = align_earnings_event(session, event, mode=mode, security_id=security_id)

    stock_returns = compute_event_forward_returns(
        session, alignment, horizons=horizons_list, source_id=stock_source_id
    )

    benchmark_returns = (
        no_benchmark_selected(stock_returns.forward_returns)
        if benchmark_id is None
        else _compute_benchmark_returns(
            session,
            benchmark_id=benchmark_id,
            stock_returns=stock_returns,
            source_id=benchmark_source_id,
        )
    )

    abnormal_returns = [
        compute_abnormal_return(stock_return=stock_return, benchmark_return=benchmark_return)
        for stock_return, benchmark_return in zip(
            stock_returns.forward_returns, benchmark_returns, strict=True
        )
    ]

    return EventStudyResult(
        event_id=event.id,
        event_fingerprint=event.fingerprint,
        company_id=event.company_id,
        research_mode=mode,
        point_in_time_eligibility=eligibility,
        eps_surprise=eps_surprise,
        alignment=alignment,
        stock_returns=stock_returns,
        benchmark_id=benchmark_id,
        benchmark_returns=benchmark_returns,
        abnormal_returns=abnormal_returns,
        price_basis_note=PRICE_BASIS_NOTE,
    )
