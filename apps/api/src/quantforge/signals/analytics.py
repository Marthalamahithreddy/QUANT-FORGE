"""Pure cross-event signal research analytics.

This module answers questions about a *population* of already-evaluated
signals: how many are evaluable, how POSITIVE/NEGATIVE/NEUTRAL signals
behaved afterward, and whether a larger EPS surprise associates with a larger
subsequent abnormal return. It is research analytics, not a backtest: there
is no cumulative return, no equity curve, no portfolio, no Sharpe ratio, no
turnover, no transaction cost -- those belong to a later strategy layer (see
the module docstrings of :mod:`quantforge.signals.eps_surprise` and
:mod:`quantforge.signals.evaluation` for where generation and evaluation stop
and this module's own scope begins).

Input, and what this module deliberately never does
-------------------------------------------------------
:func:`summarize_signal_evaluations` consumes a sequence of already-built
:class:`~quantforge.signals.evaluation.SignalEvaluation` objects. It performs
no database access, no provider calls, no ORM queries, and never regenerates
a signal or recomputes a Step 8 return -- every number it reports was already
computed by :mod:`quantforge.signals.eps_surprise` or
:mod:`quantforge.signals.evaluation` and is only aggregated here.

DESCRIPTIVE and POINT_IN_TIME are never pooled
--------------------------------------------------
The caller must state which :class:`~quantforge.research.availability.ResearchMode`
cohort it is summarizing, and every supplied evaluation must carry exactly
that mode -- a mismatch raises ``ValueError`` rather than silently producing
one blended average across two research questions that are not comparable
(see :mod:`quantforge.research.availability`'s own DESCRIPTIVE/POINT_IN_TIME
distinction). A ``POINT_IN_TIME``-ineligible signal's evaluation already
carries ``SignalEvaluationStatus.SIGNAL_UNAVAILABLE`` with no outcome
attached (see :mod:`quantforge.signals.evaluation`), so it contributes to
this module's coverage counts but never to a return statistic or a hit rate --
nothing extra is required here to enforce that; it falls out of the upstream
contract.

Horizons and signal-direction cohorts are never pooled either
-------------------------------------------------------------------
A 1-session, a 5-session, and a 20-session return are different quantities;
:func:`summarize_signal_evaluations` groups by ``horizon`` and returns one
summary per horizon. Within a horizon, ``POSITIVE``/``NEGATIVE``/``NEUTRAL``
signal-direction cohorts (see :class:`SignalDirectionCohort`) are summarized
separately from each other and from the un-stratified ``ALL`` cohort, so a
caller can never accidentally read a POSITIVE-only statistic as if it
described every signal.

Price basis and benchmark identity are inherited from Step 8, not relabelled
---------------------------------------------------------------------------
Stock and benchmark return values summarized here are Step 8's own
split-adjusted, dividend-excluding price returns (see
:mod:`quantforge.research.returns`); the abnormal return is Step 8's simple
market-adjusted abnormal return (``stock_return - benchmark_return``, implicit
beta of 1 -- never "alpha"); and if the benchmark used was SPY, it is an ETF
proxy, never "the S&P 500 Index" (see
:mod:`quantforge.research.abnormal_returns` and
:mod:`quantforge.research.benchmark_returns` for the full terminology this
module continues rather than restates).

Numeric policy: exact Decimal throughout, including correlation
---------------------------------------------------------------------
``numpy``/``scipy`` are project dependencies, but nothing in this codebase
has yet converted a financial Decimal figure to binary ``float``, and this
module does not start now: every statistic here, including the Pearson
correlation between ``signal_value`` and realized ``abnormal_return``, is
computed in exact ``Decimal`` arithmetic -- the same ambient default
``decimal`` context (28 significant digits) every other division in the
research layer already relies on, including ``Decimal.sqrt()`` for the
correlation's denominator, which stays within the ``Decimal`` domain end to
end rather than ever becoming a binary float. Introducing a ``scipy``-based
float boundary was deliberately not done here: this codebase has not yet
established an explicit, reviewed policy for where Decimal-to-float
conversion would be acceptable, and inventing one implicitly inside an
analytics helper is exactly the kind of numeric-domain drift this project's
existing modules go out of their way to avoid.

Missingness is always visible, never hidden in a denominator
------------------------------------------------------------------
Every :class:`SignalCohortSummary` carries its own :class:`CoverageCounts`
alongside its return statistics, so "mean abnormal return = 2%" is always
printed next to the sample size that produced it -- 2 observations and 2,000
observations are never allowed to look the same.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from quantforge.research.abnormal_returns import AbnormalReturnStatus
from quantforge.research.availability import ResearchMode
from quantforge.research.benchmark_returns import BenchmarkReturnStatus
from quantforge.research.returns import ForwardReturnStatus
from quantforge.signals.eps_surprise import EpsSurpriseDirection, SignalType
from quantforge.signals.evaluation import SignalEvaluation, SignalEvaluationStatus

#: A correlation coefficient is undefined with fewer than two points.
_MINIMUM_CORRELATION_PAIRS = 2

__all__ = [
    "CorrelationStatus",
    "CoverageCounts",
    "DecimalSummary",
    "HitRateResult",
    "SignalCohortSummary",
    "SignalDirectionCohort",
    "SignalValueAbnormalReturnCorrelation",
    "summarize_signal_evaluations",
]


class SignalDirectionCohort(StrEnum):
    """Which slice of a horizon's signals a :class:`SignalCohortSummary` describes.

    ``POSITIVE``/``NEGATIVE``/``NEUTRAL`` mirror
    :class:`~quantforge.signals.eps_surprise.EpsSurpriseDirection` exactly --
    a signal's cohort membership is its own ``direction`` field, nothing
    re-derived or re-thresholded here. ``ALL`` is every evaluation at this
    horizon regardless of direction (including signals whose ``direction`` is
    ``None`` because the signal itself was unavailable), used only for
    coverage/missingness accounting -- it never produces a combined
    directional hit rate (see :class:`HitRateResult`).
    """

    ALL = "ALL"
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True, slots=True)
class DecimalSummary:
    """Count/mean/median/min/max over an exact-``Decimal`` sample.

    ``count=0`` always pairs with every statistic ``None`` -- an empty sample
    never reports a fabricated zero performance figure.
    """

    count: int
    mean: Decimal | None
    median: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None


@dataclass(frozen=True, slots=True)
class CoverageCounts:
    """How many evaluations in a cohort actually had each kind of outcome.

    ``total`` includes every evaluation in the cohort, including signals that
    were themselves unavailable (``signal_unavailable``) -- those still count
    toward ``total`` and ``signal_unavailable`` but never toward any return
    statistic. This is what makes "N observations" auditable rather than
    implicit.
    """

    total: int
    signal_available: int
    signal_unavailable: int
    stock_return_available: int
    benchmark_return_available: int
    abnormal_return_available: int


@dataclass(frozen=True, slots=True)
class HitRateResult:
    """Research classification accuracy -- never called a trading win rate.

    For ``POSITIVE``, a hit is ``abnormal_return > 0``; for ``NEGATIVE``, a
    hit is ``abnormal_return < 0``; a zero abnormal return is a miss in both
    cases, never a hit. ``eligible_hit_count`` is the number of cohort
    members with an *available* abnormal return -- never inflated by members
    whose abnormal return could not be computed. For the ``ALL`` and
    ``NEUTRAL`` cohorts no directional success is invented: both fields are
    ``0`` and ``hit_rate`` is ``None``.
    """

    hit_count: int
    eligible_hit_count: int
    hit_rate: Decimal | None


class CorrelationStatus(StrEnum):
    """Why a signal_value/abnormal_return correlation could, or could not, be computed."""

    #: At least two pairs were available and both series had non-zero
    #: variance; ``correlation`` is populated.
    AVAILABLE = "AVAILABLE"

    #: Fewer than two ``(signal_value, abnormal_return)`` pairs were
    #: available. A correlation needs at least two points.
    INSUFFICIENT_PAIRS = "INSUFFICIENT_PAIRS"

    #: Every available ``signal_value`` in the sample is identical (most
    #: often: the ``NEUTRAL`` cohort, where it is always exactly zero).
    #: Correlation is mathematically undefined with zero variance on one
    #: side, never silently reported as ``0``.
    ZERO_SIGNAL_VALUE_VARIANCE = "ZERO_SIGNAL_VALUE_VARIANCE"

    #: Every available realized ``abnormal_return`` in the sample is
    #: identical. Same reasoning as above, the other axis.
    ZERO_ABNORMAL_RETURN_VARIANCE = "ZERO_ABNORMAL_RETURN_VARIANCE"


@dataclass(frozen=True, slots=True)
class SignalValueAbnormalReturnCorrelation:
    """Pearson correlation between ``signal_value`` and realized ``abnormal_return``.

    Computed in exact ``Decimal`` arithmetic (see the module docstring's
    numeric-policy note); never a regression, a p-value, a t-statistic, an
    information coefficient, or a factor loading -- just the one correlation
    coefficient over the pairs actually available.
    """

    pair_count: int
    correlation: Decimal | None
    status: CorrelationStatus
    reason: str


@dataclass(frozen=True, slots=True)
class SignalCohortSummary:
    """One horizon's, one cohort's worth of cross-event signal research analytics.

    ``stock_return``/``benchmark_return``/``abnormal_return`` are Step 8's
    own price-return definitions, summarized without recomputation: split-
    adjusted, dividend-excluding price returns, and a simple market-adjusted
    abnormal return (never "alpha"). If a benchmark was used, it is whatever
    ETF proxy (e.g. SPY) the underlying event studies were run with -- never
    relabelled as an index.
    """

    signal_type: SignalType
    research_mode: ResearchMode
    horizon: int
    cohort: SignalDirectionCohort
    coverage: CoverageCounts
    stock_return: DecimalSummary
    benchmark_return: DecimalSummary
    abnormal_return: DecimalSummary
    hit_rate: HitRateResult
    signal_value_abnormal_return_correlation: SignalValueAbnormalReturnCorrelation


def _summarize_decimals(values: list[Decimal]) -> DecimalSummary:
    if not values:
        return DecimalSummary(count=0, mean=None, median=None, minimum=None, maximum=None)

    ordered = sorted(values)
    count = len(ordered)
    mean = sum(ordered, Decimal(0)) / count
    midpoint = count // 2
    median = (
        ordered[midpoint] if count % 2 == 1 else (ordered[midpoint - 1] + ordered[midpoint]) / 2
    )
    return DecimalSummary(
        count=count, mean=mean, median=median, minimum=ordered[0], maximum=ordered[-1]
    )


def _cohort_members(
    evaluations: list[SignalEvaluation], cohort: SignalDirectionCohort
) -> list[SignalEvaluation]:
    if cohort is SignalDirectionCohort.ALL:
        return evaluations
    wanted_direction = {
        SignalDirectionCohort.POSITIVE: EpsSurpriseDirection.POSITIVE,
        SignalDirectionCohort.NEGATIVE: EpsSurpriseDirection.NEGATIVE,
        SignalDirectionCohort.NEUTRAL: EpsSurpriseDirection.NEUTRAL,
    }[cohort]
    return [evaluation for evaluation in evaluations if evaluation.direction is wanted_direction]


def _coverage_counts(members: list[SignalEvaluation]) -> CoverageCounts:
    total = len(members)
    signal_unavailable = sum(
        1 for member in members if member.status is SignalEvaluationStatus.SIGNAL_UNAVAILABLE
    )
    stock_return_available = sum(
        1
        for member in members
        if member.stock_return is not None
        and member.stock_return.status is ForwardReturnStatus.AVAILABLE
    )
    benchmark_return_available = sum(
        1
        for member in members
        if member.benchmark_return is not None
        and member.benchmark_return.status is BenchmarkReturnStatus.AVAILABLE
    )
    abnormal_return_available = sum(
        1
        for member in members
        if member.abnormal_return is not None
        and member.abnormal_return.status is AbnormalReturnStatus.AVAILABLE
    )
    return CoverageCounts(
        total=total,
        signal_available=total - signal_unavailable,
        signal_unavailable=signal_unavailable,
        stock_return_available=stock_return_available,
        benchmark_return_available=benchmark_return_available,
        abnormal_return_available=abnormal_return_available,
    )


def _available_stock_returns(members: list[SignalEvaluation]) -> list[Decimal]:
    return [
        member.stock_return.return_value
        for member in members
        if member.stock_return is not None
        and member.stock_return.status is ForwardReturnStatus.AVAILABLE
        and member.stock_return.return_value is not None
    ]


def _available_benchmark_returns(members: list[SignalEvaluation]) -> list[Decimal]:
    return [
        member.benchmark_return.benchmark_return
        for member in members
        if member.benchmark_return is not None
        and member.benchmark_return.status is BenchmarkReturnStatus.AVAILABLE
        and member.benchmark_return.benchmark_return is not None
    ]


def _available_abnormal_return_values(members: list[SignalEvaluation]) -> list[Decimal]:
    return [
        member.abnormal_return.abnormal_return
        for member in members
        if member.abnormal_return is not None
        and member.abnormal_return.status is AbnormalReturnStatus.AVAILABLE
        and member.abnormal_return.abnormal_return is not None
    ]


def _is_hit(cohort: SignalDirectionCohort, abnormal_return: Decimal) -> bool:
    """Zero is a miss on both sides: ``> 0``/``< 0``, never ``>=``/``<=``.

    Only ever called from :func:`_hit_rate` for ``POSITIVE``/``NEGATIVE`` --
    ``ALL``/``NEUTRAL`` both return before reaching this function, so those
    two members are the only precondition this private helper needs to
    satisfy.
    """
    if cohort is SignalDirectionCohort.POSITIVE:
        return abnormal_return > 0
    return abnormal_return < 0


def _hit_rate(members: list[SignalEvaluation], cohort: SignalDirectionCohort) -> HitRateResult:
    if cohort in (SignalDirectionCohort.ALL, SignalDirectionCohort.NEUTRAL):
        return HitRateResult(hit_count=0, eligible_hit_count=0, hit_rate=None)

    eligible = _available_abnormal_return_values(members)
    eligible_hit_count = len(eligible)
    if eligible_hit_count == 0:
        return HitRateResult(hit_count=0, eligible_hit_count=0, hit_rate=None)

    hit_count = sum(1 for value in eligible if _is_hit(cohort, value))
    return HitRateResult(
        hit_count=hit_count,
        eligible_hit_count=eligible_hit_count,
        hit_rate=Decimal(hit_count) / Decimal(eligible_hit_count),
    )


def _correlation_pairs(members: list[SignalEvaluation]) -> list[tuple[Decimal, Decimal]]:
    pairs: list[tuple[Decimal, Decimal]] = []
    for member in members:
        if member.signal_value is None:
            continue
        if (
            member.abnormal_return is None
            or member.abnormal_return.status is not AbnormalReturnStatus.AVAILABLE
            or member.abnormal_return.abnormal_return is None
        ):
            continue
        pairs.append((member.signal_value, member.abnormal_return.abnormal_return))
    return pairs


def _pearson_correlation(
    pairs: list[tuple[Decimal, Decimal]],
) -> SignalValueAbnormalReturnCorrelation:
    """Exact-``Decimal`` Pearson correlation. See the module docstring's numeric policy."""
    pair_count = len(pairs)
    if pair_count < _MINIMUM_CORRELATION_PAIRS:
        return SignalValueAbnormalReturnCorrelation(
            pair_count=pair_count,
            correlation=None,
            status=CorrelationStatus.INSUFFICIENT_PAIRS,
            reason="fewer than two (signal_value, abnormal_return) pairs were available",
        )

    n = Decimal(pair_count)
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    mean_x = sum(xs, Decimal(0)) / n
    mean_y = sum(ys, Decimal(0)) / n
    deviations_x = [x - mean_x for x in xs]
    deviations_y = [y - mean_y for y in ys]

    sum_dx2 = sum((dx * dx for dx in deviations_x), Decimal(0))
    if sum_dx2 == 0:
        return SignalValueAbnormalReturnCorrelation(
            pair_count=pair_count,
            correlation=None,
            status=CorrelationStatus.ZERO_SIGNAL_VALUE_VARIANCE,
            reason="signal_value is identical across every available pair",
        )

    sum_dy2 = sum((dy * dy for dy in deviations_y), Decimal(0))
    if sum_dy2 == 0:
        return SignalValueAbnormalReturnCorrelation(
            pair_count=pair_count,
            correlation=None,
            status=CorrelationStatus.ZERO_ABNORMAL_RETURN_VARIANCE,
            reason="realized abnormal_return is identical across every available pair",
        )

    sum_dxdy = sum((deviations_x[i] * deviations_y[i] for i in range(pair_count)), Decimal(0))
    denominator = (sum_dx2 * sum_dy2).sqrt()
    return SignalValueAbnormalReturnCorrelation(
        pair_count=pair_count,
        correlation=sum_dxdy / denominator,
        status=CorrelationStatus.AVAILABLE,
        reason="Pearson correlation computed in exact Decimal arithmetic",
    )


def _summarize_cohort(
    *,
    signal_type: SignalType,
    research_mode: ResearchMode,
    horizon: int,
    cohort: SignalDirectionCohort,
    members: list[SignalEvaluation],
) -> SignalCohortSummary:
    return SignalCohortSummary(
        signal_type=signal_type,
        research_mode=research_mode,
        horizon=horizon,
        cohort=cohort,
        coverage=_coverage_counts(members),
        stock_return=_summarize_decimals(_available_stock_returns(members)),
        benchmark_return=_summarize_decimals(_available_benchmark_returns(members)),
        abnormal_return=_summarize_decimals(_available_abnormal_return_values(members)),
        hit_rate=_hit_rate(members, cohort),
        signal_value_abnormal_return_correlation=_pearson_correlation(_correlation_pairs(members)),
    )


def _validate_and_deduplicate(
    evaluations: Sequence[SignalEvaluation], *, research_mode: ResearchMode
) -> list[SignalEvaluation]:
    """Enforce mode/signal-type uniformity and reject conflicting duplicate identity.

    Raises:
        ValueError: an evaluation's ``research_mode`` does not match the
            requested cohort (DESCRIPTIVE and POINT_IN_TIME must never be
            pooled); an evaluation's ``signal_type`` differs from the others
            (a future second signal family must never be silently pooled with
            this one); or two evaluations share the same
            ``(event_id, signal_type, horizon)`` identity but disagree on
            their contents (a caller bug, never silently resolved).

    An exact duplicate -- same identity *and* equal contents -- is silently
    deduplicated rather than double-counted; this is the one form of
    "cleanup" this function performs, and only because the two objects are,
    by Python dataclass equality, the same fact reported twice.

    The signal-type check below is unreachable today and marked
    ``pragma: no cover``: :class:`~quantforge.signals.eps_surprise.SignalType`
    has exactly one member, so mypy proves any two instances are the same
    literal. It is not dead by design, only by the current size of the enum
    -- the guarantee already holds, with no further change needed here, the
    instant a second signal family adds a member.
    """
    deduplicated: dict[tuple[uuid.UUID, SignalType, int], SignalEvaluation] = {}
    signal_type: SignalType | None = None

    for evaluation in evaluations:
        if evaluation.research_mode is not research_mode:
            message = (
                f"evaluation for event_id {evaluation.event_id} has research_mode "
                f"{evaluation.research_mode.value!r}, which does not match the requested "
                f"{research_mode.value!r}; DESCRIPTIVE and POINT_IN_TIME evaluations must "
                "never be pooled into one summary"
            )
            raise ValueError(message)

        if signal_type is None:
            signal_type = evaluation.signal_type
        elif (
            evaluation.signal_type is not signal_type
        ):  # pragma: no cover - unreachable while SignalType has one member
            message = (  # type: ignore[unreachable]
                f"evaluation for event_id {evaluation.event_id} has signal_type "
                f"{evaluation.signal_type.value!r}, which does not match "
                f"{signal_type.value!r}; evaluations from different signal families "
                "must not be pooled into one summary"
            )
            raise ValueError(message)

        key = (evaluation.event_id, evaluation.signal_type, evaluation.horizon)
        existing = deduplicated.get(key)
        if existing is None:
            deduplicated[key] = evaluation
        elif existing != evaluation:
            message = (
                f"conflicting duplicate evaluations supplied for event_id "
                f"{evaluation.event_id}, signal_type {evaluation.signal_type.value!r}, "
                f"horizon {evaluation.horizon}: two different SignalEvaluation objects "
                "were supplied for the same (event, signal, horizon) identity"
            )
            raise ValueError(message)
        # else: an exact duplicate of an evaluation already kept -- skip it.

    return list(deduplicated.values())


def summarize_signal_evaluations(
    evaluations: Sequence[SignalEvaluation],
    *,
    research_mode: ResearchMode,
) -> dict[int, dict[SignalDirectionCohort, SignalCohortSummary]]:
    """Summarize a population of signal evaluations, grouped by horizon and direction cohort.

    Args:
        evaluations: Already-built
            :class:`~quantforge.signals.evaluation.SignalEvaluation` objects,
            from any number of distinct earnings events, potentially spanning
            several horizons. Every evaluation must carry the same
            ``signal_type`` and the ``research_mode`` passed here -- a
            mismatch raises ``ValueError`` rather than silently pooling
            incomparable samples (see the module docstring).
        research_mode: Which research question this summary answers.
            Required explicitly rather than inferred, so a caller cannot
            accidentally summarize a mixed DESCRIPTIVE/POINT_IN_TIME sample
            without first deciding which one it meant.

    Returns:
        ``{horizon: {cohort: SignalCohortSummary}}``. Every horizon present
        in ``evaluations`` gets an entry for all four
        :class:`SignalDirectionCohort` members, even when a cohort is empty
        for that horizon (an empty cohort reports ``count=0``/``None``
        statistics, never a fabricated zero).

    Raises:
        ValueError: see :func:`_validate_and_deduplicate` -- a research-mode
            mismatch, a signal-type mismatch, or a conflicting duplicate
            ``(event_id, signal_type, horizon)`` identity.
    """
    cleaned = _validate_and_deduplicate(evaluations, research_mode=research_mode)

    by_horizon: dict[int, list[SignalEvaluation]] = {}
    for evaluation in cleaned:
        by_horizon.setdefault(evaluation.horizon, []).append(evaluation)

    return {
        horizon: {
            cohort: _summarize_cohort(
                signal_type=members[0].signal_type,
                research_mode=research_mode,
                horizon=horizon,
                cohort=cohort,
                members=_cohort_members(members, cohort),
            )
            for cohort in SignalDirectionCohort
        }
        for horizon, members in by_horizon.items()
    }
