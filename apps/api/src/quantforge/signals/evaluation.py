"""Signal evaluation: joining an already-generated signal to realized outcomes.

This module answers a question structurally forbidden to
:mod:`quantforge.signals.eps_surprise`: *given a signal that was already
generated from historically knowable information, how did the stock,
benchmark, and abnormal return actually turn out?*

Generation and evaluation are kept apart on purpose
------------------------------------------------------
::

    historically knowable information
            |
            v
         signal                      (quantforge.signals.eps_surprise)

    already-generated signal
            +
    future realized outcomes
            |
            v
        evaluation                   (this module)

Future outcomes may enter evaluation -- that is its entire purpose. They must
never enter generation, and this module does not let them: it takes an
already-built :class:`~quantforge.signals.eps_surprise.EpsSurpriseSignal` and
an already-built
:class:`~quantforge.research.event_study.EventStudyResult` as plain inputs and
performs no database access and no Step 8 recomputation of its own --
:func:`~quantforge.research.event_study.analyze_earnings_event` already
computed the stock, benchmark and abnormal returns; this module only reads
them.

Nothing here is a backtest. It evaluates one signal against one event's
outcomes, horizon by horizon. There is no aggregation across events, no hit
rate, no mean return by direction, no Sharpe ratio, no cumulative return --
those belong to a later research/strategy layer.

Join identity
--------------
A signal and an outcome are only evaluated together when they provably
describe the *same* earnings observation: :func:`evaluate_eps_surprise_signal`
checks ``signal.event_id == outcomes.event_id`` (never ticker, company, or
event-date matching, which could silently pair two different revisions of the
same fiscal period) and, where both sides resolved a listing, that the
resolved ``listing_id`` agrees too. Either mismatch raises ``ValueError`` --
a caller-contract violation on the same footing as
:func:`~quantforge.research.abnormal_returns.compute_abnormal_return`'s own
horizon-mismatch check, not a normal missing-data state.

Future-revision protection
-----------------------------
A signal's ``signal_value``/``direction``/``effective_session`` are already
frozen at generation time, from one already-selected
:class:`~quantforge.models.earnings.EarningsEvent` row (see that module's own
revision-selection discipline). This module never re-derives them from
``outcomes`` and never substitutes a different revision's EPS facts -- it only
reads realized prices, which are a different axis entirely from which
earnings revision was used.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from quantforge.research.abnormal_returns import AbnormalReturn, AbnormalReturnStatus
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.research.benchmark_returns import BenchmarkReturn
from quantforge.research.event_study import EventStudyResult
from quantforge.research.returns import ForwardReturn, ForwardReturnStatus
from quantforge.signals.eps_surprise import (
    EpsSurpriseDirection,
    EpsSurpriseSignal,
    EpsSurpriseSignalStatus,
    SignalType,
)

__all__ = ["SignalEvaluation", "SignalEvaluationStatus", "evaluate_eps_surprise_signal"]


class SignalEvaluationStatus(StrEnum):
    """Why a signal's evaluation against realized outcomes could, or could not, complete."""

    #: The signal was ``AVAILABLE`` and both the stock and benchmark legs
    #: (and therefore the abnormal return) were available for this horizon.
    AVAILABLE = "AVAILABLE"

    #: The signal itself was not ``AVAILABLE`` (missing EPS inputs, no
    #: effective session, point-in-time ineligible, ...). No outcome is
    #: attached -- see :func:`_evaluate_one_horizon`'s docstring note.
    SIGNAL_UNAVAILABLE = "SIGNAL_UNAVAILABLE"

    #: The signal was available but the stock's own forward return for this
    #: horizon was not (e.g. insufficient forward sessions).
    STOCK_RETURN_UNAVAILABLE = "STOCK_RETURN_UNAVAILABLE"

    #: The stock return was available but the benchmark return was not (no
    #: benchmark selected, a missing endpoint, an ambiguous source, ...).
    #: ``stock_return`` is still attached and usable on its own -- a missing
    #: benchmark never erases a valid stock outcome.
    BENCHMARK_RETURN_UNAVAILABLE = "BENCHMARK_RETURN_UNAVAILABLE"

    #: Both legs were individually available but the abnormal return itself
    #: could not be computed. Unreachable under the current Step 8
    #: ``compute_abnormal_return`` contract (a plain subtraction of two
    #: available returns cannot fail), kept so this enum's vocabulary stays
    #: total if that contract ever grows a new failure mode.
    ABNORMAL_RETURN_UNAVAILABLE = "ABNORMAL_RETURN_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class SignalEvaluation:
    """How one EPS-surprise signal's classification compared to one horizon's realized outcome.

    ``stock_return``/``benchmark_return``/``abnormal_return`` are the full
    Step 8 sub-results, not flattened numeric fields, so a caller inspecting
    why an evaluation is partial or unavailable never has to re-fetch them to
    find out which leg failed -- the same convention
    :class:`~quantforge.research.abnormal_returns.AbnormalReturn` already uses
    for its own two legs. All three are ``None`` only when ``status`` is
    ``SIGNAL_UNAVAILABLE``: with no usable signal, there is nothing for a
    realized outcome to be evaluated against, so none is attached (see the
    module docstring's point-in-time-eligibility discussion). In every other
    case they are the real Step 8 objects for this horizon, even when one of
    them individually reports its own unavailability -- a valid stock outcome
    is never erased just because the benchmark leg was unavailable.
    """

    signal_type: SignalType
    event_id: uuid.UUID
    research_mode: ResearchMode
    point_in_time_eligibility: PointInTimeEligibility
    signal_status: EpsSurpriseSignalStatus
    direction: EpsSurpriseDirection | None
    signal_value: Decimal | None
    horizon: int
    stock_return: ForwardReturn | None
    benchmark_return: BenchmarkReturn | None
    abnormal_return: AbnormalReturn | None
    status: SignalEvaluationStatus
    reason: str


def _evaluate_one_horizon(
    signal: EpsSurpriseSignal,
    stock_return: ForwardReturn,
    benchmark_return: BenchmarkReturn,
    abnormal_return: AbnormalReturn,
) -> SignalEvaluation:
    if signal.status is not EpsSurpriseSignalStatus.AVAILABLE:
        # Deliberately does not attach the realized outcome objects: Step
        # 8.1/8.2's own point-in-time discipline already refuses to call an
        # ineligible observation tradable, and attaching a real future return
        # to a signal that was never actually produced would read as "this
        # ineligible signal has a realized outcome anyway" -- exactly the
        # thing POINT_IN_TIME mode exists to prevent. See the module and
        # package docstrings: future outcomes may enter evaluation, but an
        # evaluation is not a backdoor that upgrades an unavailable signal.
        return SignalEvaluation(
            signal_type=signal.signal_type,
            event_id=signal.event_id,
            research_mode=signal.research_mode,
            point_in_time_eligibility=signal.point_in_time_eligibility,
            signal_status=signal.status,
            direction=None,
            signal_value=None,
            horizon=stock_return.horizon,
            stock_return=None,
            benchmark_return=None,
            abnormal_return=None,
            status=SignalEvaluationStatus.SIGNAL_UNAVAILABLE,
            reason=(
                f"the EPS-surprise signal itself is unavailable ({signal.status.value}); "
                "no outcome can be evaluated against a signal that was never produced"
            ),
        )

    if stock_return.status is not ForwardReturnStatus.AVAILABLE:
        eval_status = SignalEvaluationStatus.STOCK_RETURN_UNAVAILABLE
        reason = "the stock forward return for this horizon was not available"
    elif abnormal_return.status is AbnormalReturnStatus.AVAILABLE:
        eval_status = SignalEvaluationStatus.AVAILABLE
        reason = "the signal and every outcome leg were available for this horizon"
    else:
        # abnormal_return.status must be BENCHMARK_RETURN_UNAVAILABLE here:
        # AbnormalReturnStatus.STOCK_RETURN_UNAVAILABLE is excluded by the
        # branch above (compute_abnormal_return derives it from this same
        # stock_return object), and AVAILABLE was just handled, leaving only
        # BENCHMARK_RETURN_UNAVAILABLE among AbnormalReturnStatus's three
        # members. SignalEvaluationStatus.ABNORMAL_RETURN_UNAVAILABLE is
        # reserved for a future AbnormalReturnStatus failure mode this
        # contract does not have yet (see its own docstring) and is
        # therefore never assigned here.
        eval_status = SignalEvaluationStatus.BENCHMARK_RETURN_UNAVAILABLE
        reason = "the benchmark return for this horizon was not available"

    return SignalEvaluation(
        signal_type=signal.signal_type,
        event_id=signal.event_id,
        research_mode=signal.research_mode,
        point_in_time_eligibility=signal.point_in_time_eligibility,
        signal_status=signal.status,
        direction=signal.direction,
        signal_value=signal.signal_value,
        horizon=stock_return.horizon,
        stock_return=stock_return,
        benchmark_return=benchmark_return,
        abnormal_return=abnormal_return,
        status=eval_status,
        reason=reason,
    )


def evaluate_eps_surprise_signal(
    signal: EpsSurpriseSignal,
    outcomes: EventStudyResult,
) -> list[SignalEvaluation]:
    """Evaluate one EPS-surprise signal against its event's realized outcomes.

    Args:
        signal: An already-generated signal (see
            :func:`~quantforge.signals.eps_surprise.generate_eps_surprise_signal`).
            Never re-derived or recomputed here.
        outcomes: The already-computed
            :class:`~quantforge.research.event_study.EventStudyResult` for the
            *same* earnings observation (see
            :func:`~quantforge.research.event_study.analyze_earnings_event`).
            Every requested horizon in ``outcomes`` produces exactly one
            :class:`SignalEvaluation`, in the same order.

    Raises:
        ValueError: ``signal.event_id`` does not match ``outcomes.event_id``,
            the research modes differ (a DESCRIPTIVE signal is never evaluated
            against POINT_IN_TIME outcomes, or vice versa), or both sides
            resolved a listing and those listings disagree.
            Either is a caller bug -- pairing a signal with a different
            earnings observation's outcomes -- never a normal missing-data
            state, so it is not folded into :class:`SignalEvaluationStatus`.

    Returns:
        One :class:`SignalEvaluation` per horizon present in ``outcomes``,
        in order. Never aggregates across horizons or events.
    """
    if signal.event_id != outcomes.event_id:
        message = (
            f"signal event_id {signal.event_id} does not match EventStudyResult "
            f"event_id {outcomes.event_id}; refusing to evaluate a signal against "
            "a different earnings observation"
        )
        raise ValueError(message)

    if signal.research_mode is not outcomes.research_mode:
        message = (
            f"signal research_mode {signal.research_mode.value} does not match "
            f"EventStudyResult research_mode {outcomes.research_mode.value}; refusing to "
            "evaluate a signal against outcomes produced under a different research mode"
        )
        raise ValueError(message)

    outcome_listing_id = outcomes.alignment.listing_resolution.listing_id
    if (
        signal.listing_id is not None
        and outcome_listing_id is not None
        and signal.listing_id != outcome_listing_id
    ):
        message = (
            f"signal listing_id {signal.listing_id} does not match EventStudyResult "
            f"listing_id {outcome_listing_id} despite a matching event_id; refusing "
            "to evaluate a signal against outcomes resolved for a different listing"
        )
        raise ValueError(message)

    return [
        _evaluate_one_horizon(signal, stock_return, benchmark_return, abnormal_return)
        for stock_return, benchmark_return, abnormal_return in zip(
            outcomes.stock_returns.forward_returns,
            outcomes.benchmark_returns,
            outcomes.abnormal_returns,
            strict=True,
        )
    ]
