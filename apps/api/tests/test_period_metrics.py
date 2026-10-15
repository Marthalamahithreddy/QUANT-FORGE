"""Revenue and net income: current beside the prior-year comparative, and every named refusal.

Pure, no database. Figures are synthetic test inputs only; the real Nike filing is exercised by the
local smoke test against the validation database.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from quantforge.financials.comparative_eps import ComparativeEpsStatus
from quantforge.financials.eps import EpsInputFact, EpsPeriodClass, EpsStatus
from quantforge.financials.eps_change import EpsChangeStatus, PercentageUnavailableReason
from quantforge.financials.period_metrics import (
    MONEY_TAG,
    MoneyComparison,
    MoneyMetric,
    PointStatus,
    compare_money_metric,
)

TAG = MONEY_TAG[MoneyMetric.REVENUE]
ACC = "0000320187-26-000037"
END = date(2026, 2, 28)
PRIOR = date(2025, 2, 28)


def fact(
    value: str | None,
    *,
    period_end: date = END,
    qtrs: int = 1,
    unit: str = "USD",
    version: str = "us-gaap/2025",
    segments: str = "",
    coreg: str = "",
    tag: str = TAG,
) -> EpsInputFact:
    return EpsInputFact(
        fact_id=uuid.uuid4(),
        accession_number=ACC,
        tag=tag,
        taxonomy_version=version,
        unit=unit,
        period_end=period_end,
        duration_quarters=qtrs,
        value=None if value is None else Decimal(value),
        segments=segments,
        coreg=coreg,
    )


def compare(
    facts: list[EpsInputFact],
    *,
    ends: tuple[date, ...] = (END,),
    metric: MoneyMetric = MoneyMetric.REVENUE,
) -> MoneyComparison:
    return compare_money_metric(
        metric=metric,
        submission_period_ends=ends,
        facts=facts,
        period_class=EpsPeriodClass.QUARTER,
    )


def test_current_and_prior_with_exact_change_and_ratio() -> None:
    result = compare([fact("11279000000.0000"), fact("11269000000.0000", period_end=PRIOR)])
    assert result.current.status is PointStatus.AVAILABLE
    assert result.prior.status is PointStatus.AVAILABLE
    assert result.change.status is EpsChangeStatus.AVAILABLE
    assert result.change.absolute == Decimal("10000000.0000")
    assert result.change.ratio is not None
    assert str(result.change.ratio).startswith("0.000887390185")


def test_year_to_date_and_other_durations_are_never_used() -> None:
    result = compare([fact("35426000000", qtrs=3), fact("35212000000", qtrs=3, period_end=PRIOR)])
    assert result.current.status is PointStatus.MISSING
    assert result.prior.status is PointStatus.CURRENT_UNAVAILABLE
    assert result.change.status is EpsChangeStatus.INPUT_UNAVAILABLE


def test_a_loss_has_a_change_but_no_ratio_against_a_negative_prior() -> None:
    # Facts of another concept are never substituted: revenue-tagged facts are not net income.
    other = compare([fact("-5"), fact("-10", period_end=PRIOR)], metric=MoneyMetric.NET_INCOME)
    assert other.current.status is PointStatus.MISSING
    income = MONEY_TAG[MoneyMetric.NET_INCOME]
    loss = compare(
        [fact("-5", tag=income), fact("-10", period_end=PRIOR, tag=income)],
        metric=MoneyMetric.NET_INCOME,
    )
    assert loss.change.absolute == Decimal("5")
    assert loss.change.ratio is None
    assert loss.change.ratio_unavailable_reason is PercentageUnavailableReason.NEGATIVE_COMPARATIVE


def test_a_zero_prior_has_no_ratio() -> None:
    result = compare([fact("5"), fact("0", period_end=PRIOR)])
    assert result.change.absolute == Decimal("5")
    assert result.change.ratio_unavailable_reason is PercentageUnavailableReason.ZERO_COMPARATIVE


def test_equal_values_give_a_zero_change_and_a_zero_ratio() -> None:
    result = compare([fact("7"), fact("7", period_end=PRIOR)])
    assert result.change.absolute == Decimal(0)
    assert result.change.ratio == Decimal(0)


@pytest.mark.parametrize(
    ("facts", "status"),
    [
        ([fact("1", version="0000320187-26-000037")], PointStatus.EXTENSION_ONLY),
        ([fact("1", segments="Axis=Member")], PointStatus.DIMENSIONAL_ONLY),
        ([fact("1", coreg="OTHERCO")], PointStatus.COREG_ONLY),
        ([fact("1", unit="EUR")], PointStatus.WRONG_UNIT),
        ([fact(None)], PointStatus.NULL_VALUE),
        ([fact("1"), fact("1", version="us-gaap/2024")], PointStatus.AMBIGUOUS),
        ([], PointStatus.MISSING),
    ],
)
def test_each_current_refusal_is_named_and_blocks_the_comparison(
    facts: list[EpsInputFact], status: PointStatus
) -> None:
    result = compare(facts)
    assert result.current.status is status
    assert result.prior.status is PointStatus.CURRENT_UNAVAILABLE
    assert result.change.status is EpsChangeStatus.INPUT_UNAVAILABLE


def test_a_prior_that_is_not_twelve_month_ends_earlier_is_refused_not_tolerated() -> None:
    result = compare([fact("1"), fact("1", period_end=date(2025, 3, 1))])
    assert result.prior.status is PointStatus.PERIOD_MISMATCH
    assert result.change.status is EpsChangeStatus.INPUT_UNAVAILABLE


def test_no_earlier_fact_is_a_missing_prior_and_the_current_is_kept() -> None:
    result = compare([fact("1")])
    assert result.current.status is PointStatus.AVAILABLE
    assert result.prior.status is PointStatus.MISSING


def test_two_prior_candidates_are_ambiguous_even_when_equal() -> None:
    result = compare(
        [
            fact("1"),
            fact("2", period_end=PRIOR),
            fact("2", period_end=PRIOR, version="us-gaap/2024"),
        ]
    )
    assert result.prior.status is PointStatus.AMBIGUOUS


def test_nothing_ingested_or_disagreeing_submissions_are_refused() -> None:
    assert compare([fact("1")], ends=()).current.status is PointStatus.NOT_INGESTED
    assert compare([fact("1")], ends=(END, PRIOR)).current.status is PointStatus.AMBIGUOUS


def test_the_point_vocabulary_covers_every_eps_status() -> None:
    for status in EpsStatus:
        assert PointStatus(status.value)
    for comparative in ComparativeEpsStatus:
        name = comparative.value.removeprefix("COMPARATIVE_")
        assert PointStatus(name)
