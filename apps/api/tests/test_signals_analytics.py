"""Pure unit tests for cross-event signal research analytics.

Everything in ``quantforge.signals.analytics`` is pure (no database, no
provider, no ORM), so every test here builds plain
:class:`~quantforge.signals.evaluation.SignalEvaluation` objects (and their
nested Step 8 sub-results) directly in memory.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from quantforge.research.abnormal_returns import AbnormalReturn, AbnormalReturnStatus
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.research.benchmark_returns import BenchmarkReturn, BenchmarkReturnStatus
from quantforge.research.returns import ForwardReturn, ForwardReturnStatus
from quantforge.signals.analytics import (
    CorrelationStatus,
    SignalDirectionCohort,
    summarize_signal_evaluations,
)
from quantforge.signals.eps_surprise import (
    EpsSurpriseDirection,
    EpsSurpriseSignalStatus,
    SignalType,
)
from quantforge.signals.evaluation import SignalEvaluation, SignalEvaluationStatus

_ANCHOR = date(2026, 1, 7)
_TARGET = date(2026, 1, 8)
_BENCHMARK_ID = uuid.uuid4()


def _forward_return(
    *, horizon: int = 1, value: Decimal | None = Decimal("0.10"), available: bool = True
) -> ForwardReturn:
    if not available:
        return ForwardReturn(
            horizon=horizon,
            anchor_session=_ANCHOR,
            anchor_close=Decimal("100"),
            target_session=None,
            target_close=None,
            return_value=None,
            status=ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS,
            reason="not enough sessions",
        )
    return ForwardReturn(
        horizon=horizon,
        anchor_session=_ANCHOR,
        anchor_close=Decimal("100"),
        target_session=_TARGET,
        target_close=Decimal("100") * (1 + value),  # type: ignore[operator]
        return_value=value,
        status=ForwardReturnStatus.AVAILABLE,
        reason="computed",
    )


def _benchmark_return(
    *, horizon: int = 1, value: Decimal | None = Decimal("0.02"), available: bool = True
) -> BenchmarkReturn:
    if not available:
        return BenchmarkReturn(
            benchmark_id=_BENCHMARK_ID,
            horizon=horizon,
            anchor_session=_ANCHOR,
            anchor_close=Decimal("50"),
            target_session=None,
            target_close=None,
            benchmark_return=None,
            status=BenchmarkReturnStatus.MISSING_TARGET_PRICE,
            reason="missing endpoint",
        )
    return BenchmarkReturn(
        benchmark_id=_BENCHMARK_ID,
        horizon=horizon,
        anchor_session=_ANCHOR,
        anchor_close=Decimal("50"),
        target_session=_TARGET,
        target_close=Decimal("50") * (1 + value),  # type: ignore[operator]
        benchmark_return=value,
        status=BenchmarkReturnStatus.AVAILABLE,
        reason="computed",
    )


def _abnormal_return(
    *,
    stock_return: ForwardReturn,
    benchmark_return: BenchmarkReturn,
    value: Decimal | None,
    status: AbnormalReturnStatus = AbnormalReturnStatus.AVAILABLE,
) -> AbnormalReturn:
    return AbnormalReturn(
        horizon=stock_return.horizon,
        stock_return=stock_return,
        benchmark_return=benchmark_return,
        abnormal_return=value,
        status=status,
        reason="computed" if status is AbnormalReturnStatus.AVAILABLE else "unavailable",
    )


def _evaluation(
    *,
    event_id: uuid.UUID | None = None,
    horizon: int = 1,
    research_mode: ResearchMode = ResearchMode.DESCRIPTIVE,
    point_in_time_eligibility: PointInTimeEligibility = PointInTimeEligibility.NOT_APPLICABLE,
    signal_status: EpsSurpriseSignalStatus = EpsSurpriseSignalStatus.AVAILABLE,
    direction: EpsSurpriseDirection | None = EpsSurpriseDirection.POSITIVE,
    signal_value: Decimal | None = Decimal("0.20"),
    stock_return: ForwardReturn | None = None,
    benchmark_return: BenchmarkReturn | None = None,
    abnormal_return: AbnormalReturn | None = None,
    status: SignalEvaluationStatus = SignalEvaluationStatus.AVAILABLE,
    signal_type: SignalType = SignalType.EPS_SURPRISE,
) -> SignalEvaluation:
    sr = stock_return if stock_return is not None else _forward_return(horizon=horizon)
    br = benchmark_return if benchmark_return is not None else _benchmark_return(horizon=horizon)
    ar = (
        abnormal_return
        if abnormal_return is not None
        else _abnormal_return(stock_return=sr, benchmark_return=br, value=Decimal("0.08"))
    )
    return SignalEvaluation(
        signal_type=signal_type,
        event_id=event_id if event_id is not None else uuid.uuid4(),
        research_mode=research_mode,
        point_in_time_eligibility=point_in_time_eligibility,
        signal_status=signal_status,
        direction=direction,
        signal_value=signal_value,
        horizon=horizon,
        stock_return=sr if status is not SignalEvaluationStatus.SIGNAL_UNAVAILABLE else None,
        benchmark_return=br if status is not SignalEvaluationStatus.SIGNAL_UNAVAILABLE else None,
        abnormal_return=ar if status is not SignalEvaluationStatus.SIGNAL_UNAVAILABLE else None,
        status=status,
        reason="test evaluation",
    )


def _unavailable_signal_evaluation(
    *, event_id: uuid.UUID | None = None, horizon: int = 1
) -> SignalEvaluation:
    return _evaluation(
        event_id=event_id,
        horizon=horizon,
        research_mode=ResearchMode.POINT_IN_TIME,
        point_in_time_eligibility=PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE,
        signal_status=EpsSurpriseSignalStatus.NOT_POINT_IN_TIME_ELIGIBLE,
        direction=None,
        signal_value=None,
        status=SignalEvaluationStatus.SIGNAL_UNAVAILABLE,
    )


# ── summary statistics ───────────────────────────────────────────────────────


def test_single_observation_summary() -> None:
    evaluation = _evaluation(signal_value=Decimal("0.2"))
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)

    all_summary = result[1][SignalDirectionCohort.ALL]
    assert all_summary.stock_return.count == 1
    assert all_summary.stock_return.mean == Decimal("0.10")
    assert all_summary.stock_return.median == Decimal("0.10")
    assert all_summary.stock_return.minimum == Decimal("0.10")
    assert all_summary.stock_return.maximum == Decimal("0.10")


def test_odd_n_median_is_the_middle_value() -> None:
    evaluations = [
        _evaluation(stock_return=_forward_return(value=Decimal("0.10"))),
        _evaluation(stock_return=_forward_return(value=Decimal("0.30"))),
        _evaluation(stock_return=_forward_return(value=Decimal("0.20"))),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)

    assert result[1][SignalDirectionCohort.ALL].stock_return.median == Decimal("0.20")


def test_even_n_median_is_the_mean_of_the_two_middle_values() -> None:
    evaluations = [
        _evaluation(stock_return=_forward_return(value=Decimal("0.10"))),
        _evaluation(stock_return=_forward_return(value=Decimal("0.20"))),
        _evaluation(stock_return=_forward_return(value=Decimal("0.30"))),
        _evaluation(stock_return=_forward_return(value=Decimal("0.40"))),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)

    # Middle two are 0.20 and 0.30 -> mean 0.25, exact Decimal arithmetic.
    assert result[1][SignalDirectionCohort.ALL].stock_return.median == Decimal("0.25")


def test_positive_and_negative_values_are_both_represented_correctly() -> None:
    evaluations = [
        _evaluation(stock_return=_forward_return(value=Decimal("-0.10"))),
        _evaluation(stock_return=_forward_return(value=Decimal("0.10"))),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)

    summary = result[1][SignalDirectionCohort.ALL].stock_return
    assert summary.mean == Decimal("0")
    assert summary.minimum == Decimal("-0.10")
    assert summary.maximum == Decimal("0.10")


def test_mean_is_exact_decimal_not_rounded_through_float() -> None:
    evaluations = [
        _evaluation(stock_return=_forward_return(value=Decimal("0.10"))),
        _evaluation(stock_return=_forward_return(value=Decimal("0.10"))),
        _evaluation(stock_return=_forward_return(value=Decimal("0.10"))),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)

    mean = result[1][SignalDirectionCohort.ALL].stock_return.mean
    assert mean == Decimal("0.10")
    assert isinstance(mean, Decimal)


def test_empty_available_sample_reports_none_not_fake_zero() -> None:
    evaluations = [_unavailable_signal_evaluation()]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.POINT_IN_TIME)

    summary = result[1][SignalDirectionCohort.ALL].stock_return
    assert summary.count == 0
    assert summary.mean is None
    assert summary.median is None
    assert summary.minimum is None
    assert summary.maximum is None


def test_partial_missing_outcomes_are_excluded_from_statistics_but_counted() -> None:
    sr_missing = _forward_return(available=False)
    br_missing = _benchmark_return(available=False)
    ar_unavailable = _abnormal_return(
        stock_return=sr_missing,
        benchmark_return=br_missing,
        value=None,
        status=AbnormalReturnStatus.STOCK_RETURN_UNAVAILABLE,
    )
    evaluations = [
        _evaluation(stock_return=_forward_return(value=Decimal("0.10"))),
        _evaluation(
            stock_return=sr_missing,
            benchmark_return=br_missing,
            abnormal_return=ar_unavailable,
            status=SignalEvaluationStatus.STOCK_RETURN_UNAVAILABLE,
        ),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)

    summary = result[1][SignalDirectionCohort.ALL]
    assert summary.coverage.total == 2
    assert summary.coverage.stock_return_available == 1
    assert summary.stock_return.count == 1


def test_horizons_1_5_20_are_kept_separate() -> None:
    evaluations = [
        _evaluation(horizon=1, stock_return=_forward_return(horizon=1, value=Decimal("0.01"))),
        _evaluation(horizon=5, stock_return=_forward_return(horizon=5, value=Decimal("0.05"))),
        _evaluation(horizon=20, stock_return=_forward_return(horizon=20, value=Decimal("0.20"))),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)

    assert set(result.keys()) == {1, 5, 20}
    assert result[1][SignalDirectionCohort.ALL].stock_return.mean == Decimal("0.01")
    assert result[5][SignalDirectionCohort.ALL].stock_return.mean == Decimal("0.05")
    assert result[20][SignalDirectionCohort.ALL].stock_return.mean == Decimal("0.20")


# ── cohorts ──────────────────────────────────────────────────────────────────


def test_cohorts_never_leak_direction() -> None:
    evaluations = [
        _evaluation(direction=EpsSurpriseDirection.POSITIVE, signal_value=Decimal("0.1")),
        _evaluation(direction=EpsSurpriseDirection.NEGATIVE, signal_value=Decimal("-0.1")),
        _evaluation(direction=EpsSurpriseDirection.NEUTRAL, signal_value=Decimal("0")),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)[1]

    assert result[SignalDirectionCohort.ALL].coverage.total == 3
    assert result[SignalDirectionCohort.POSITIVE].coverage.total == 1
    assert result[SignalDirectionCohort.NEGATIVE].coverage.total == 1
    assert result[SignalDirectionCohort.NEUTRAL].coverage.total == 1


def test_unavailable_signals_do_not_enter_any_direction_cohort() -> None:
    evaluations = [
        _unavailable_signal_evaluation(),
        _evaluation(
            direction=EpsSurpriseDirection.POSITIVE,
            research_mode=ResearchMode.POINT_IN_TIME,
            point_in_time_eligibility=PointInTimeEligibility.ELIGIBLE,
        ),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.POINT_IN_TIME)[1]

    assert result[SignalDirectionCohort.ALL].coverage.total == 2
    assert result[SignalDirectionCohort.ALL].coverage.signal_unavailable == 1
    assert result[SignalDirectionCohort.POSITIVE].coverage.total == 1
    assert result[SignalDirectionCohort.NEGATIVE].coverage.total == 0
    assert result[SignalDirectionCohort.NEUTRAL].coverage.total == 0


# ── mode separation ──────────────────────────────────────────────────────────


def test_descriptive_only_sample_summarizes_normally() -> None:
    evaluations = [_evaluation(research_mode=ResearchMode.DESCRIPTIVE) for _ in range(3)]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)

    assert result[1][SignalDirectionCohort.ALL].coverage.total == 3
    assert result[1][SignalDirectionCohort.ALL].research_mode is ResearchMode.DESCRIPTIVE


def test_point_in_time_eligible_only_sample_summarizes_normally() -> None:
    evaluations = [
        _evaluation(
            research_mode=ResearchMode.POINT_IN_TIME,
            point_in_time_eligibility=PointInTimeEligibility.ELIGIBLE,
        )
        for _ in range(3)
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.POINT_IN_TIME)

    assert result[1][SignalDirectionCohort.ALL].coverage.total == 3


def test_mixed_descriptive_and_point_in_time_is_rejected() -> None:
    evaluations = [
        _evaluation(research_mode=ResearchMode.DESCRIPTIVE),
        _evaluation(
            research_mode=ResearchMode.POINT_IN_TIME,
            point_in_time_eligibility=PointInTimeEligibility.ELIGIBLE,
        ),
    ]
    with pytest.raises(ValueError, match="research_mode"):
        summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)


def test_point_in_time_ineligible_observations_count_for_coverage_not_performance() -> None:
    evaluations = [
        _unavailable_signal_evaluation(),
        _evaluation(
            research_mode=ResearchMode.POINT_IN_TIME,
            point_in_time_eligibility=PointInTimeEligibility.ELIGIBLE,
            stock_return=_forward_return(value=Decimal("0.5")),
        ),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.POINT_IN_TIME)
    summary = result[1][SignalDirectionCohort.ALL]

    assert summary.coverage.total == 2
    assert summary.coverage.signal_unavailable == 1
    assert summary.coverage.signal_available == 1
    # The ineligible observation's (nonexistent) return never enters the mean.
    assert summary.stock_return.count == 1
    assert summary.stock_return.mean == Decimal("0.5")


# ── hit rate ─────────────────────────────────────────────────────────────────


def test_positive_with_positive_abnormal_is_a_hit() -> None:
    sr = _forward_return()
    br = _benchmark_return()
    ar = _abnormal_return(stock_return=sr, benchmark_return=br, value=Decimal("0.05"))
    evaluation = _evaluation(
        direction=EpsSurpriseDirection.POSITIVE,
        stock_return=sr,
        benchmark_return=br,
        abnormal_return=ar,
    )
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)
    hit_rate = result[1][SignalDirectionCohort.POSITIVE].hit_rate
    assert hit_rate.hit_count == 1
    assert hit_rate.eligible_hit_count == 1
    assert hit_rate.hit_rate == Decimal("1")


def test_positive_with_negative_abnormal_is_a_miss() -> None:
    sr = _forward_return()
    br = _benchmark_return()
    ar = _abnormal_return(stock_return=sr, benchmark_return=br, value=Decimal("-0.05"))
    evaluation = _evaluation(
        direction=EpsSurpriseDirection.POSITIVE,
        stock_return=sr,
        benchmark_return=br,
        abnormal_return=ar,
    )
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)
    hit_rate = result[1][SignalDirectionCohort.POSITIVE].hit_rate
    assert hit_rate.hit_count == 0
    assert hit_rate.hit_rate == Decimal("0")


def test_positive_with_zero_abnormal_is_a_miss() -> None:
    sr = _forward_return()
    br = _benchmark_return()
    ar = _abnormal_return(stock_return=sr, benchmark_return=br, value=Decimal("0"))
    evaluation = _evaluation(
        direction=EpsSurpriseDirection.POSITIVE,
        stock_return=sr,
        benchmark_return=br,
        abnormal_return=ar,
    )
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)
    hit_rate = result[1][SignalDirectionCohort.POSITIVE].hit_rate
    assert hit_rate.hit_count == 0
    assert hit_rate.hit_rate == Decimal("0")


def test_negative_with_negative_abnormal_is_a_hit() -> None:
    sr = _forward_return()
    br = _benchmark_return()
    ar = _abnormal_return(stock_return=sr, benchmark_return=br, value=Decimal("-0.05"))
    evaluation = _evaluation(
        direction=EpsSurpriseDirection.NEGATIVE,
        signal_value=Decimal("-0.1"),
        stock_return=sr,
        benchmark_return=br,
        abnormal_return=ar,
    )
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)
    hit_rate = result[1][SignalDirectionCohort.NEGATIVE].hit_rate
    assert hit_rate.hit_count == 1
    assert hit_rate.hit_rate == Decimal("1")


def test_negative_with_positive_abnormal_is_a_miss() -> None:
    sr = _forward_return()
    br = _benchmark_return()
    ar = _abnormal_return(stock_return=sr, benchmark_return=br, value=Decimal("0.05"))
    evaluation = _evaluation(
        direction=EpsSurpriseDirection.NEGATIVE,
        signal_value=Decimal("-0.1"),
        stock_return=sr,
        benchmark_return=br,
        abnormal_return=ar,
    )
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)
    hit_rate = result[1][SignalDirectionCohort.NEGATIVE].hit_rate
    assert hit_rate.hit_count == 0
    assert hit_rate.hit_rate == Decimal("0")


def test_negative_with_zero_abnormal_is_a_miss() -> None:
    sr = _forward_return()
    br = _benchmark_return()
    ar = _abnormal_return(stock_return=sr, benchmark_return=br, value=Decimal("0"))
    evaluation = _evaluation(
        direction=EpsSurpriseDirection.NEGATIVE,
        signal_value=Decimal("-0.1"),
        stock_return=sr,
        benchmark_return=br,
        abnormal_return=ar,
    )
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)
    hit_rate = result[1][SignalDirectionCohort.NEGATIVE].hit_rate
    assert hit_rate.hit_count == 0
    assert hit_rate.hit_rate == Decimal("0")


def test_neutral_cohort_has_no_hit_rate() -> None:
    evaluation = _evaluation(direction=EpsSurpriseDirection.NEUTRAL, signal_value=Decimal("0"))
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)
    hit_rate = result[1][SignalDirectionCohort.NEUTRAL].hit_rate
    assert hit_rate.hit_rate is None
    assert hit_rate.hit_count == 0
    assert hit_rate.eligible_hit_count == 0


def test_all_cohort_never_has_a_combined_directional_hit_rate() -> None:
    evaluations = [
        _evaluation(direction=EpsSurpriseDirection.POSITIVE),
        _evaluation(direction=EpsSurpriseDirection.NEGATIVE, signal_value=Decimal("-0.1")),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)
    hit_rate = result[1][SignalDirectionCohort.ALL].hit_rate
    assert hit_rate.hit_rate is None


def test_missing_abnormal_return_excluded_from_hit_denominator_but_counted_in_coverage() -> None:
    sr = _forward_return()
    br = _benchmark_return(available=False)
    ar = _abnormal_return(
        stock_return=sr,
        benchmark_return=br,
        value=None,
        status=AbnormalReturnStatus.BENCHMARK_RETURN_UNAVAILABLE,
    )
    evaluation = _evaluation(
        direction=EpsSurpriseDirection.POSITIVE,
        stock_return=sr,
        benchmark_return=br,
        abnormal_return=ar,
        status=SignalEvaluationStatus.BENCHMARK_RETURN_UNAVAILABLE,
    )
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)
    summary = result[1][SignalDirectionCohort.POSITIVE]

    assert summary.coverage.total == 1
    assert summary.coverage.abnormal_return_available == 0
    assert summary.hit_rate.eligible_hit_count == 0
    assert summary.hit_rate.hit_rate is None


# ── correlation ──────────────────────────────────────────────────────────────


def test_perfect_positive_correlation() -> None:
    evaluations = [
        _evaluation(
            signal_value=Decimal("0.1"),
            stock_return=_forward_return(value=Decimal("0.1")),
            benchmark_return=_benchmark_return(value=Decimal("0")),
            abnormal_return=_abnormal_return(
                stock_return=_forward_return(value=Decimal("0.1")),
                benchmark_return=_benchmark_return(value=Decimal("0")),
                value=Decimal("0.1"),
            ),
        ),
        _evaluation(
            signal_value=Decimal("0.2"),
            stock_return=_forward_return(value=Decimal("0.2")),
            benchmark_return=_benchmark_return(value=Decimal("0")),
            abnormal_return=_abnormal_return(
                stock_return=_forward_return(value=Decimal("0.2")),
                benchmark_return=_benchmark_return(value=Decimal("0")),
                value=Decimal("0.2"),
            ),
        ),
        _evaluation(
            signal_value=Decimal("0.3"),
            stock_return=_forward_return(value=Decimal("0.3")),
            benchmark_return=_benchmark_return(value=Decimal("0")),
            abnormal_return=_abnormal_return(
                stock_return=_forward_return(value=Decimal("0.3")),
                benchmark_return=_benchmark_return(value=Decimal("0")),
                value=Decimal("0.3"),
            ),
        ),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)
    correlation = result[1][SignalDirectionCohort.ALL].signal_value_abnormal_return_correlation

    assert correlation.status is CorrelationStatus.AVAILABLE
    assert correlation.pair_count == 3
    assert correlation.correlation == Decimal("1")


def test_perfect_negative_correlation() -> None:
    def _pair(signal_value: Decimal, abnormal: Decimal) -> SignalEvaluation:
        sr = _forward_return(value=abnormal)
        br = _benchmark_return(value=Decimal("0"))
        return _evaluation(
            signal_value=signal_value,
            stock_return=sr,
            benchmark_return=br,
            abnormal_return=_abnormal_return(stock_return=sr, benchmark_return=br, value=abnormal),
        )

    evaluations = [
        _pair(Decimal("0.1"), Decimal("0.3")),
        _pair(Decimal("0.2"), Decimal("0.2")),
        _pair(Decimal("0.3"), Decimal("0.1")),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)
    correlation = result[1][SignalDirectionCohort.ALL].signal_value_abnormal_return_correlation

    assert correlation.correlation == Decimal("-1")


def test_non_perfect_relationship_produces_a_correlation_strictly_between_bounds() -> None:
    def _pair(signal_value: Decimal, abnormal: Decimal) -> SignalEvaluation:
        sr = _forward_return(value=abnormal)
        br = _benchmark_return(value=Decimal("0"))
        return _evaluation(
            signal_value=signal_value,
            stock_return=sr,
            benchmark_return=br,
            abnormal_return=_abnormal_return(stock_return=sr, benchmark_return=br, value=abnormal),
        )

    evaluations = [
        _pair(Decimal("0.1"), Decimal("0.05")),
        _pair(Decimal("0.2"), Decimal("0.01")),
        _pair(Decimal("0.3"), Decimal("0.4")),
        _pair(Decimal("-0.1"), Decimal("-0.2")),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)
    correlation = result[1][SignalDirectionCohort.ALL].signal_value_abnormal_return_correlation

    assert correlation.status is CorrelationStatus.AVAILABLE
    assert correlation.correlation is not None
    assert Decimal("-1") < correlation.correlation < Decimal("1")


def test_missing_pair_is_excluded_from_correlation() -> None:
    sr_missing = _forward_return(available=False)
    br_missing = _benchmark_return(available=False)
    ar_missing = _abnormal_return(
        stock_return=sr_missing,
        benchmark_return=br_missing,
        value=None,
        status=AbnormalReturnStatus.STOCK_RETURN_UNAVAILABLE,
    )
    evaluations = [
        _evaluation(
            signal_value=Decimal("0.1"),
            stock_return=sr_missing,
            benchmark_return=br_missing,
            abnormal_return=ar_missing,
            status=SignalEvaluationStatus.STOCK_RETURN_UNAVAILABLE,
        ),
        _evaluation(
            signal_value=Decimal("0.2"),
            stock_return=_forward_return(value=Decimal("0.2")),
            benchmark_return=_benchmark_return(value=Decimal("0")),
            abnormal_return=_abnormal_return(
                stock_return=_forward_return(value=Decimal("0.2")),
                benchmark_return=_benchmark_return(value=Decimal("0")),
                value=Decimal("0.2"),
            ),
        ),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)
    correlation = result[1][SignalDirectionCohort.ALL].signal_value_abnormal_return_correlation

    assert correlation.pair_count == 1
    assert correlation.status is CorrelationStatus.INSUFFICIENT_PAIRS


def test_fewer_than_two_pairs_is_insufficient() -> None:
    evaluation = _evaluation(signal_value=Decimal("0.1"))
    result = summarize_signal_evaluations([evaluation], research_mode=ResearchMode.DESCRIPTIVE)
    correlation = result[1][SignalDirectionCohort.ALL].signal_value_abnormal_return_correlation

    assert correlation.pair_count == 1
    assert correlation.correlation is None
    assert correlation.status is CorrelationStatus.INSUFFICIENT_PAIRS


def test_zero_signal_value_variance_is_explicit_not_fabricated_zero() -> None:
    def _pair(abnormal: Decimal) -> SignalEvaluation:
        sr = _forward_return(value=abnormal)
        br = _benchmark_return(value=Decimal("0"))
        return _evaluation(
            direction=EpsSurpriseDirection.NEUTRAL,
            signal_value=Decimal("0"),
            stock_return=sr,
            benchmark_return=br,
            abnormal_return=_abnormal_return(stock_return=sr, benchmark_return=br, value=abnormal),
        )

    evaluations = [_pair(Decimal("0.1")), _pair(Decimal("0.2"))]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)
    correlation = result[1][SignalDirectionCohort.NEUTRAL].signal_value_abnormal_return_correlation

    assert correlation.status is CorrelationStatus.ZERO_SIGNAL_VALUE_VARIANCE
    assert correlation.correlation is None


def test_zero_abnormal_return_variance_is_explicit_not_fabricated_zero() -> None:
    def _pair(signal_value: Decimal) -> SignalEvaluation:
        sr = _forward_return(value=Decimal("0.05"))
        br = _benchmark_return(value=Decimal("0"))
        return _evaluation(
            signal_value=signal_value,
            stock_return=sr,
            benchmark_return=br,
            abnormal_return=_abnormal_return(
                stock_return=sr, benchmark_return=br, value=Decimal("0.05")
            ),
        )

    evaluations = [_pair(Decimal("0.1")), _pair(Decimal("0.2"))]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)
    correlation = result[1][SignalDirectionCohort.ALL].signal_value_abnormal_return_correlation

    assert correlation.status is CorrelationStatus.ZERO_ABNORMAL_RETURN_VARIANCE
    assert correlation.correlation is None


def test_correlation_retains_decimal_precision_and_never_uses_float() -> None:
    """Exact Decimal inputs (no repeating-decimal division) must round-trip to exactly 1.

    This isolates "does the pipeline preserve Decimal precision end to end"
    from the separate, expected fact that a *repeating* decimal like 1/3 is
    itself only finite-precision-approximate in any Decimal context -- that
    is a property of representing 1/3 at all, not a defect in this module.
    """

    def _pair(signal_value: Decimal, abnormal: Decimal) -> SignalEvaluation:
        sr = _forward_return(value=abnormal)
        br = _benchmark_return(value=Decimal("0"))
        return _evaluation(
            signal_value=signal_value,
            stock_return=sr,
            benchmark_return=br,
            abnormal_return=_abnormal_return(stock_return=sr, benchmark_return=br, value=abnormal),
        )

    evaluations = [
        _pair(Decimal("0.1234"), Decimal("0.3702")),  # 3 * signal_value, exact
        _pair(Decimal("0.2345"), Decimal("0.7035")),
        _pair(Decimal("0.3456"), Decimal("1.0368")),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)
    correlation = result[1][SignalDirectionCohort.ALL].signal_value_abnormal_return_correlation

    assert correlation.status is CorrelationStatus.AVAILABLE
    assert isinstance(correlation.correlation, Decimal)
    assert correlation.correlation == Decimal("1")  # perfectly linear, no float drift


# ── duplicates ───────────────────────────────────────────────────────────────


def test_exact_duplicate_evaluation_is_not_double_counted() -> None:
    event_id = uuid.uuid4()
    evaluation = _evaluation(event_id=event_id, horizon=1)
    result = summarize_signal_evaluations(
        [evaluation, evaluation], research_mode=ResearchMode.DESCRIPTIVE
    )

    assert result[1][SignalDirectionCohort.ALL].coverage.total == 1


def test_conflicting_duplicate_identity_is_rejected() -> None:
    event_id = uuid.uuid4()
    first = _evaluation(event_id=event_id, horizon=1, direction=EpsSurpriseDirection.POSITIVE)
    second = _evaluation(event_id=event_id, horizon=1, direction=EpsSurpriseDirection.NEGATIVE)

    with pytest.raises(ValueError, match="conflicting duplicate"):
        summarize_signal_evaluations([first, second], research_mode=ResearchMode.DESCRIPTIVE)


def test_same_event_different_horizon_is_not_a_duplicate() -> None:
    event_id = uuid.uuid4()
    evaluations = [
        _evaluation(event_id=event_id, horizon=1),
        _evaluation(event_id=event_id, horizon=5, stock_return=_forward_return(horizon=5)),
    ]
    result = summarize_signal_evaluations(evaluations, research_mode=ResearchMode.DESCRIPTIVE)

    assert result[1][SignalDirectionCohort.ALL].coverage.total == 1
    assert result[5][SignalDirectionCohort.ALL].coverage.total == 1


def test_no_silent_double_counting_across_many_identical_submissions() -> None:
    event_id = uuid.uuid4()
    evaluation = _evaluation(event_id=event_id, horizon=1)
    result = summarize_signal_evaluations([evaluation] * 5, research_mode=ResearchMode.DESCRIPTIVE)

    assert result[1][SignalDirectionCohort.ALL].coverage.total == 1
