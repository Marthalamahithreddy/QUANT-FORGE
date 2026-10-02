"""Pure unit tests for signal evaluation.

:func:`evaluate_eps_surprise_signal` takes plain, already-computed objects --
no database session is involved anywhere in
``quantforge.signals.evaluation`` -- so every test here builds an
:class:`~quantforge.signals.eps_surprise.EpsSurpriseSignal` and an
:class:`~quantforge.research.event_study.EventStudyResult` directly from
in-memory values, reusing the real Step 8 pure compute functions
(:func:`~quantforge.research.returns.compute_forward_returns`,
:func:`~quantforge.research.benchmark_returns.compute_benchmark_return_for_stock_return`,
:func:`~quantforge.research.abnormal_returns.compute_abnormal_return`) to
produce realistic sub-objects rather than hand-rolling them.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from quantforge.models.enums import EarningsTiming
from quantforge.research.abnormal_returns import compute_abnormal_return
from quantforge.research.alignment import EventStudyAlignment
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.research.benchmark_returns import (
    compute_benchmark_return_for_stock_return,
    no_benchmark_selected,
)
from quantforge.research.calendar import AlignmentStatus, SessionAlignment
from quantforge.research.eps_surprise import EpsSurpriseResult, EpsSurpriseStatus
from quantforge.research.event_study import PRICE_BASIS_NOTE, EventStudyResult
from quantforge.research.listing_resolution import ListingResolution, ListingResolutionStatus
from quantforge.research.returns import EventForwardReturns, ForwardReturn, compute_forward_returns
from quantforge.signals.eps_surprise import (
    EpsSurpriseDirection,
    EpsSurpriseSignal,
    EpsSurpriseSignalStatus,
    SignalType,
)
from quantforge.signals.evaluation import SignalEvaluationStatus, evaluate_eps_surprise_signal

_EVENT_ID = uuid.uuid4()
_OTHER_EVENT_ID = uuid.uuid4()
_COMPANY_ID = uuid.uuid4()
_SECURITY_ID = uuid.uuid4()
_LISTING_ID = uuid.uuid4()
_OTHER_LISTING_ID = uuid.uuid4()
_BENCHMARK_ID = uuid.uuid4()
_EVENT_DATE = date(2026, 1, 7)
_ANCHOR = date(2026, 1, 7)


def _sessions(count: int) -> list[date]:
    """Consecutive calendar dates. Real weekday/holiday gaps are irrelevant here:
    this module reads only the ordered session list ``compute_forward_returns``
    already produced, never the calendar itself."""
    return [_ANCHOR + timedelta(days=i) for i in range(count)]


def _signal(**overrides: object) -> EpsSurpriseSignal:
    fields: dict[str, object] = {
        "signal_type": SignalType.EPS_SURPRISE,
        "event_id": _EVENT_ID,
        "company_id": _COMPANY_ID,
        "security_id": _SECURITY_ID,
        "listing_id": _LISTING_ID,
        "research_mode": ResearchMode.DESCRIPTIVE,
        "point_in_time_eligibility": PointInTimeEligibility.NOT_APPLICABLE,
        "event_date": _EVENT_DATE,
        "timing": EarningsTiming.BEFORE_MARKET,
        "effective_session": _ANCHOR,
        "status": EpsSurpriseSignalStatus.AVAILABLE,
        "direction": EpsSurpriseDirection.POSITIVE,
        "signal_value": Decimal("0.2000"),
        "reported_eps": Decimal("1.2000"),
        "estimated_eps": Decimal("1.0000"),
        "absolute_surprise": Decimal("0.2000"),
        "surprise_ratio": Decimal("0.2000"),
        "reason": "test signal",
    }
    fields.update(overrides)
    return EpsSurpriseSignal(**fields)  # type: ignore[arg-type]


def _alignment(*, listing_id: uuid.UUID | None = _LISTING_ID) -> EventStudyAlignment:
    listing_resolution = ListingResolution(
        status=ListingResolutionStatus.RESOLVED
        if listing_id is not None
        else (ListingResolutionStatus.NO_LISTING_FOR_DATE),
        listing_id=listing_id,
        security_id=_SECURITY_ID if listing_id is not None else None,
        reason="test resolution",
    )
    session_alignment = SessionAlignment(
        event_session=_ANCHOR,
        first_post_event_session=_ANCHOR,
        status=AlignmentStatus.ALIGNED,
        reason="test alignment",
    )
    return EventStudyAlignment(
        event_id=_EVENT_ID,
        event_fingerprint="test-fingerprint",
        company_id=_COMPANY_ID,
        event_date=_EVENT_DATE,
        event_timestamp=None,
        timing=EarningsTiming.BEFORE_MARKET,
        research_mode=ResearchMode.DESCRIPTIVE,
        point_in_time_eligibility=PointInTimeEligibility.NOT_APPLICABLE,
        listing_resolution=listing_resolution,
        session_alignment=session_alignment,
    )


def _outcomes(
    *,
    event_id: uuid.UUID = _EVENT_ID,
    listing_id: uuid.UUID | None = _LISTING_ID,
    horizons: tuple[int, ...] = (1,),
    stock_prices: dict[date, Decimal],
    benchmark_prices: dict[date, Decimal] | None = None,
    benchmark_id: uuid.UUID | None = _BENCHMARK_ID,
) -> EventStudyResult:
    forward_returns = compute_forward_returns(
        anchor_session=_ANCHOR,
        price_series=list(stock_prices.items()),
        horizons=list(horizons),
    )
    stock_returns = EventForwardReturns(
        event_id=event_id,
        listing_id=listing_id,
        price_source_id=None,
        anchor_session=_ANCHOR,
        forward_returns=forward_returns,
    )
    benchmark_returns = (
        no_benchmark_selected(forward_returns)
        if benchmark_id is None
        else [
            compute_benchmark_return_for_stock_return(
                benchmark_id=benchmark_id,
                stock_return=forward_return,
                prices=benchmark_prices or {},
            )
            for forward_return in forward_returns
        ]
    )
    abnormal_returns = [
        compute_abnormal_return(stock_return=sr, benchmark_return=br)
        for sr, br in zip(forward_returns, benchmark_returns, strict=True)
    ]
    eps_surprise = EpsSurpriseResult(
        status=EpsSurpriseStatus.AVAILABLE,
        reported_eps=Decimal("1.2000"),
        estimated_eps=Decimal("1.0000"),
        absolute_surprise=Decimal("0.2000"),
        surprise_ratio=Decimal("0.2000"),
        reason="test",
    )
    return EventStudyResult(
        event_id=event_id,
        event_fingerprint="test-fingerprint",
        company_id=_COMPANY_ID,
        research_mode=ResearchMode.DESCRIPTIVE,
        point_in_time_eligibility=PointInTimeEligibility.NOT_APPLICABLE,
        eps_surprise=eps_surprise,
        alignment=_alignment(listing_id=listing_id),
        stock_returns=stock_returns,
        benchmark_id=benchmark_id,
        benchmark_returns=benchmark_returns,
        abnormal_returns=abnormal_returns,
        price_basis_note=PRICE_BASIS_NOTE,
    )


# ── fully available, both legs ──────────────────────────────────────────────


def test_available_signal_with_all_outcomes_available() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    benchmark_prices = {sessions[0]: Decimal("50"), sessions[1]: Decimal("52")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=benchmark_prices)

    evaluations = evaluate_eps_surprise_signal(_signal(), outcomes)

    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert evaluation.status is SignalEvaluationStatus.AVAILABLE
    assert evaluation.horizon == 1
    assert evaluation.signal_value == Decimal("0.2000")
    assert evaluation.direction is EpsSurpriseDirection.POSITIVE
    assert evaluation.stock_return is not None
    assert evaluation.stock_return.return_value == Decimal("0.1")  # 110/100 - 1
    assert evaluation.benchmark_return is not None
    assert evaluation.benchmark_return.benchmark_return == Decimal("0.04")  # 52/50 - 1
    assert evaluation.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return == Decimal("0.06")


def test_positive_signal_with_positive_abnormal_return() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("120")}
    benchmark_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("105")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=benchmark_prices)

    evaluation = evaluate_eps_surprise_signal(
        _signal(direction=EpsSurpriseDirection.POSITIVE), outcomes
    )[0]

    assert evaluation.direction is EpsSurpriseDirection.POSITIVE
    assert evaluation.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return > 0


def test_positive_signal_with_negative_abnormal_return() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("101")}
    benchmark_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=benchmark_prices)

    evaluation = evaluate_eps_surprise_signal(
        _signal(direction=EpsSurpriseDirection.POSITIVE), outcomes
    )[0]

    assert evaluation.direction is EpsSurpriseDirection.POSITIVE
    assert evaluation.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return < 0


def test_negative_signal_with_negative_abnormal_return() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("80")}
    benchmark_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("95")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=benchmark_prices)

    evaluation = evaluate_eps_surprise_signal(
        _signal(direction=EpsSurpriseDirection.NEGATIVE, signal_value=Decimal("-0.1")), outcomes
    )[0]

    assert evaluation.direction is EpsSurpriseDirection.NEGATIVE
    assert evaluation.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return < 0


def test_negative_signal_with_positive_abnormal_return() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("98")}
    benchmark_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("80")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=benchmark_prices)

    evaluation = evaluate_eps_surprise_signal(
        _signal(direction=EpsSurpriseDirection.NEGATIVE, signal_value=Decimal("-0.1")), outcomes
    )[0]

    assert evaluation.direction is EpsSurpriseDirection.NEGATIVE
    assert evaluation.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return > 0


def test_neutral_signal_evaluates_normally() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("105")}
    benchmark_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("102")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=benchmark_prices)

    evaluation = evaluate_eps_surprise_signal(
        _signal(direction=EpsSurpriseDirection.NEUTRAL, signal_value=Decimal("0")), outcomes
    )[0]

    assert evaluation.status is SignalEvaluationStatus.AVAILABLE
    assert evaluation.direction is EpsSurpriseDirection.NEUTRAL


# ── horizons ─────────────────────────────────────────────────────────────────


def test_multiple_horizons_each_produce_their_own_evaluation() -> None:
    sessions = _sessions(21)
    stock_prices = {day: Decimal("100") + i for i, day in enumerate(sessions)}
    benchmark_prices = {day: Decimal("50") for day in sessions}
    outcomes = _outcomes(
        horizons=(1, 5, 20), stock_prices=stock_prices, benchmark_prices=benchmark_prices
    )

    evaluations = evaluate_eps_surprise_signal(_signal(), outcomes)

    assert [e.horizon for e in evaluations] == [1, 5, 20]
    assert all(e.status is SignalEvaluationStatus.AVAILABLE for e in evaluations)


def test_missing_stock_horizon_is_insufficient_history() -> None:
    """Horizon 20 requested but only 2 sessions of price history exist."""
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(horizons=(1, 20), stock_prices=stock_prices, benchmark_prices=stock_prices)

    evaluations = evaluate_eps_surprise_signal(_signal(), outcomes)

    by_horizon = {e.horizon: e for e in evaluations}
    assert by_horizon[1].status is SignalEvaluationStatus.AVAILABLE
    assert by_horizon[20].status is SignalEvaluationStatus.STOCK_RETURN_UNAVAILABLE
    assert by_horizon[20].stock_return is not None  # preserved for audit, not erased
    assert by_horizon[20].abnormal_return is not None


# ── partial outcome: stock available, benchmark missing ────────────────────


def test_missing_benchmark_endpoint_preserves_valid_stock_outcome() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    # Benchmark has no observation for the target session at all.
    benchmark_prices = {sessions[0]: Decimal("50")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=benchmark_prices)

    evaluation = evaluate_eps_surprise_signal(_signal(), outcomes)[0]

    assert evaluation.status is SignalEvaluationStatus.BENCHMARK_RETURN_UNAVAILABLE
    assert evaluation.stock_return is not None
    assert evaluation.stock_return.return_value == Decimal("0.1")
    assert evaluation.benchmark_return is not None
    assert evaluation.benchmark_return.benchmark_return is None
    assert evaluation.abnormal_return is not None
    assert evaluation.abnormal_return.abnormal_return is None


def test_no_benchmark_selected_is_an_unavailable_abnormal_return() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_id=None)

    evaluation = evaluate_eps_surprise_signal(_signal(), outcomes)[0]

    assert evaluation.status is SignalEvaluationStatus.BENCHMARK_RETURN_UNAVAILABLE
    assert evaluation.stock_return is not None
    assert evaluation.stock_return.return_value == Decimal("0.1")


# ── descriptive vs point-in-time ─────────────────────────────────────────────


def test_descriptive_signal_evaluates_with_mode_preserved() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=stock_prices)

    evaluation = evaluate_eps_surprise_signal(
        _signal(
            research_mode=ResearchMode.DESCRIPTIVE,
            point_in_time_eligibility=PointInTimeEligibility.NOT_APPLICABLE,
        ),
        outcomes,
    )[0]

    assert evaluation.status is SignalEvaluationStatus.AVAILABLE
    assert evaluation.research_mode is ResearchMode.DESCRIPTIVE
    assert evaluation.point_in_time_eligibility is PointInTimeEligibility.NOT_APPLICABLE


def test_point_in_time_eligible_signal_can_be_available() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=stock_prices)

    evaluation = evaluate_eps_surprise_signal(
        _signal(
            research_mode=ResearchMode.POINT_IN_TIME,
            point_in_time_eligibility=PointInTimeEligibility.ELIGIBLE,
        ),
        outcomes,
    )[0]

    assert evaluation.status is SignalEvaluationStatus.AVAILABLE
    assert evaluation.point_in_time_eligibility is PointInTimeEligibility.ELIGIBLE


def test_point_in_time_ineligible_signal_remains_unavailable() -> None:
    """Future outcomes existing must never upgrade an ineligible signal."""
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("999")}  # dramatic move
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=stock_prices)

    evaluation = evaluate_eps_surprise_signal(
        _signal(
            status=EpsSurpriseSignalStatus.NOT_POINT_IN_TIME_ELIGIBLE,
            direction=None,
            signal_value=None,
            research_mode=ResearchMode.POINT_IN_TIME,
            point_in_time_eligibility=PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE,
        ),
        outcomes,
    )[0]

    assert evaluation.status is SignalEvaluationStatus.SIGNAL_UNAVAILABLE
    assert evaluation.stock_return is None
    assert evaluation.benchmark_return is None
    assert evaluation.abnormal_return is None
    assert evaluation.direction is None
    assert evaluation.signal_value is None


@pytest.mark.parametrize(
    "status",
    [
        EpsSurpriseSignalStatus.MISSING_REPORTED_EPS,
        EpsSurpriseSignalStatus.MISSING_ESTIMATED_EPS,
        EpsSurpriseSignalStatus.UNAVAILABLE_ZERO_ESTIMATE,
        EpsSurpriseSignalStatus.NO_EFFECTIVE_SESSION,
        EpsSurpriseSignalStatus.UNSUPPORTED_TIMING,
    ],
)
def test_every_unavailable_signal_status_yields_signal_unavailable_evaluation(
    status: EpsSurpriseSignalStatus,
) -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=stock_prices)

    evaluation = evaluate_eps_surprise_signal(
        _signal(status=status, direction=None, signal_value=None), outcomes
    )[0]

    assert evaluation.status is SignalEvaluationStatus.SIGNAL_UNAVAILABLE
    assert evaluation.stock_return is None


# ── identity-mismatch rejection ──────────────────────────────────────────────


def test_mismatched_event_id_is_rejected() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(
        event_id=_OTHER_EVENT_ID, stock_prices=stock_prices, benchmark_prices=stock_prices
    )

    with pytest.raises(ValueError, match="does not match"):
        evaluate_eps_surprise_signal(_signal(event_id=_EVENT_ID), outcomes)


def test_mismatched_listing_id_with_matching_event_id_is_rejected() -> None:
    """event_id alone is not sufficient identity proof if the resolved listing disagrees."""
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(
        event_id=_EVENT_ID,
        listing_id=_OTHER_LISTING_ID,
        stock_prices=stock_prices,
        benchmark_prices=stock_prices,
    )

    with pytest.raises(ValueError, match="listing_id"):
        evaluate_eps_surprise_signal(_signal(event_id=_EVENT_ID, listing_id=_LISTING_ID), outcomes)


def test_matching_event_id_with_one_side_unresolved_listing_is_not_rejected() -> None:
    """A None listing_id on either side is not a mismatch -- only a disagreement between two
    actually-resolved listings is."""
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(
        event_id=_EVENT_ID,
        listing_id=None,
        stock_prices=stock_prices,
        benchmark_prices=stock_prices,
    )

    evaluations = evaluate_eps_surprise_signal(
        _signal(event_id=_EVENT_ID, listing_id=_LISTING_ID), outcomes
    )

    assert evaluations[0].status is SignalEvaluationStatus.AVAILABLE


# ── look-ahead separation: outcomes may vary, generation facts may not ─────


def test_different_realized_prices_change_evaluation_but_not_signal_facts() -> None:
    signal = _signal()
    sessions = _sessions(2)

    low_outcomes = _outcomes(
        stock_prices={sessions[0]: Decimal("100"), sessions[1]: Decimal("101")},
        benchmark_prices={sessions[0]: Decimal("100"), sessions[1]: Decimal("100")},
    )
    high_outcomes = _outcomes(
        stock_prices={sessions[0]: Decimal("100"), sessions[1]: Decimal("500")},
        benchmark_prices={sessions[0]: Decimal("100"), sessions[1]: Decimal("100")},
    )

    low_evaluation = evaluate_eps_surprise_signal(signal, low_outcomes)[0]
    high_evaluation = evaluate_eps_surprise_signal(signal, high_outcomes)[0]

    # The evaluation outcome genuinely differs...
    assert low_evaluation.stock_return != high_evaluation.stock_return
    assert low_evaluation.abnormal_return != high_evaluation.abnormal_return

    # ...but the signal's own generation-time facts are untouched by either call.
    assert signal.signal_value == Decimal("0.2000")
    assert signal.direction is EpsSurpriseDirection.POSITIVE
    assert signal.effective_session == _ANCHOR
    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    # And both evaluations still echo those same, unchanged signal-side facts.
    assert low_evaluation.signal_value == high_evaluation.signal_value == Decimal("0.2000")
    assert low_evaluation.direction == high_evaluation.direction == EpsSurpriseDirection.POSITIVE


# ── future revision tripwire ─────────────────────────────────────────────────


def test_a_later_revisions_facts_cannot_substitute_the_evaluated_signals_facts() -> None:
    """Signal A (preliminary) is evaluated; a later revision B's facts never leak in.

    This module has no mechanism to even look up "revision B" -- there is no
    database access and no event re-fetch -- so the only way this could fail
    is if ``evaluate_eps_surprise_signal`` recomputed the EPS surprise itself
    instead of trusting the ``EpsSurpriseSignal`` it was handed. It does not.
    """
    preliminary_signal = _signal(
        direction=EpsSurpriseDirection.POSITIVE,
        signal_value=Decimal("0.1000"),
        surprise_ratio=Decimal("0.1000"),
        reported_eps=Decimal("1.1000"),
    )
    # outcomes.eps_surprise independently describes a hypothetical later
    # restatement with a flipped sign -- proving the evaluation never reads
    # EPS facts from `outcomes` at all, only from the signal it was given.
    sessions = _sessions(2)
    outcomes = _outcomes(
        stock_prices={sessions[0]: Decimal("100"), sessions[1]: Decimal("110")},
        benchmark_prices={sessions[0]: Decimal("100"), sessions[1]: Decimal("100")},
    )
    restated_eps_surprise = EpsSurpriseResult(
        status=EpsSurpriseStatus.AVAILABLE,
        reported_eps=Decimal("0.5000"),
        estimated_eps=Decimal("1.0000"),
        absolute_surprise=Decimal("-0.5000"),
        surprise_ratio=Decimal("-0.5000"),
        reason="hypothetical later restatement",
    )
    outcomes_with_restated_eps = EventStudyResult(
        event_id=outcomes.event_id,
        event_fingerprint=outcomes.event_fingerprint,
        company_id=outcomes.company_id,
        research_mode=outcomes.research_mode,
        point_in_time_eligibility=outcomes.point_in_time_eligibility,
        eps_surprise=restated_eps_surprise,
        alignment=outcomes.alignment,
        stock_returns=outcomes.stock_returns,
        benchmark_id=outcomes.benchmark_id,
        benchmark_returns=outcomes.benchmark_returns,
        abnormal_returns=outcomes.abnormal_returns,
        price_basis_note=outcomes.price_basis_note,
    )

    evaluation = evaluate_eps_surprise_signal(preliminary_signal, outcomes_with_restated_eps)[0]

    assert evaluation.direction is EpsSurpriseDirection.POSITIVE
    assert evaluation.signal_value == Decimal("0.1000")
    # The realized price outcome is still evaluated normally.
    assert evaluation.status is SignalEvaluationStatus.AVAILABLE
    assert evaluation.stock_return is not None
    assert evaluation.stock_return.return_value == Decimal("0.1")


def test_evaluate_eps_surprise_signal_never_reads_outcomes_eps_surprise_field() -> None:
    """Structural proof: evaluation never touches ``EventStudyResult.eps_surprise`` at all."""
    sessions = _sessions(2)
    outcomes = _outcomes(
        stock_prices={sessions[0]: Decimal("100"), sessions[1]: Decimal("110")},
        benchmark_prices={sessions[0]: Decimal("100"), sessions[1]: Decimal("100")},
    )
    sentinel_outcomes = EventStudyResult(
        event_id=outcomes.event_id,
        event_fingerprint=outcomes.event_fingerprint,
        company_id=outcomes.company_id,
        research_mode=outcomes.research_mode,
        point_in_time_eligibility=outcomes.point_in_time_eligibility,
        eps_surprise="not-a-real-eps-surprise-object",  # type: ignore[arg-type]
        alignment=outcomes.alignment,
        stock_returns=outcomes.stock_returns,
        benchmark_id=outcomes.benchmark_id,
        benchmark_returns=outcomes.benchmark_returns,
        abnormal_returns=outcomes.abnormal_returns,
        price_basis_note=outcomes.price_basis_note,
    )

    # Does not raise despite eps_surprise being structurally invalid: proof
    # the field is never read.
    evaluations = evaluate_eps_surprise_signal(_signal(), sentinel_outcomes)

    assert evaluations[0].status is SignalEvaluationStatus.AVAILABLE


# ── sub-objects are never flattened away ─────────────────────────────────────


def test_full_forward_return_object_is_preserved_not_flattened() -> None:
    sessions = _sessions(2)
    stock_prices = {sessions[0]: Decimal("100"), sessions[1]: Decimal("110")}
    outcomes = _outcomes(stock_prices=stock_prices, benchmark_prices=stock_prices)

    evaluation = evaluate_eps_surprise_signal(_signal(), outcomes)[0]

    assert isinstance(evaluation.stock_return, ForwardReturn)
    assert evaluation.stock_return.anchor_session == _ANCHOR
    assert evaluation.stock_return.target_session == sessions[1]
